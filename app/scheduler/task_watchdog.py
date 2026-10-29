"""Watch one crawl from a separate process, without limiting its total duration."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

logger = logging.getLogger(__name__)
_HEARTBEAT_INTERVAL = 15.0
_HEARTBEAT_TIMEOUT = 600.0
_CLOSE_TIMEOUT = 2.0
FailureRecorder = Callable[[str, str, str, str], Awaitable[None]]


class WatchdogCleanupError(RuntimeError):
    """The worker must exit instead of accepting another task with a live watchdog."""


class TaskWatchdog:
    def __init__(self, task_id: str, delivery_id: str | None) -> None:
        self.task_id = task_id
        self.delivery_id = delivery_id
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._heartbeat: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._owner: asyncio.Task | None = None
        self.failure: RuntimeError | None = None

    def start(self, run_id: str) -> None:
        if self.delivery_id is None or self._process is not None:
            return
        for value in (self.task_id, run_id, self.delivery_id):
            UUID(value)
        self._loop = asyncio.get_running_loop()
        self._owner = asyncio.current_task()
        self._process = subprocess.Popen(
            [sys.executable, "-m", __name__, str(os.getpid()),
             self.task_id, run_id, self.delivery_id],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, bufsize=0,
            start_new_session=sys.platform != "win32",
        )
        self._beat()

    def check(self) -> None:
        if self.failure is not None:
            raise self.failure
        if self._process is not None and self._process.poll() is not None:
            raise RuntimeError("Task heartbeat watchdog exited unexpectedly")

    def _beat(self) -> None:
        # This callback must run on the crawl's loop, never on a logging thread.
        if self._owner is not None and self._owner.done():
            return
        try:
            with self._lock:
                process = self._process
                if process is None:
                    return
                self.check()
                process.stdin.write(b"HEARTBEAT\n")
        except (RuntimeError, OSError):
            self.failure = RuntimeError("Task heartbeat watchdog is unavailable")
            if self._owner is not None:
                self._owner.cancel()
            return
        self._heartbeat = self._loop.call_later(_HEARTBEAT_INTERVAL, self._beat)

    def stop_heartbeat(self) -> None:
        """Keep guarding cleanup without allowing idle loop ticks to mask it."""
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            self._heartbeat = None

    def close(self) -> None:
        self.stop_heartbeat()
        with self._lock:
            process, self._process = self._process, None
            if process is None:
                return
            try:
                try:
                    process.stdin.write(b"DONE\n")
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    process.stdin.close()
                try:
                    process.wait(timeout=_CLOSE_TIMEOUT)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=_CLOSE_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise WatchdogCleanupError("Task watchdog could not be reaped") from exc


async def _record_failure(task_id: str, run_id: str, delivery_id: str, reason: str) -> None:
    # The watchdog owns its connection and never uses the worker's loop or pool.
    import asyncpg

    from app.storage.postgres_client import _resolved_pg_url

    connection = None
    try:
        async with asyncio.timeout(20):
            connection = await asyncpg.connect(
                _resolved_pg_url().replace("postgresql+asyncpg://", "postgresql://"),
                timeout=5, command_timeout=5,
                server_settings={"statement_timeout": "5000", "lock_timeout": "2000"},
            )
            async with connection.transaction():
                task = await connection.fetchrow(
                    """SELECT id FROM public.automation_tasks
                       WHERE id=$1::uuid AND current_run_id=$2::uuid AND celery_task_id=$3
                         AND status IN ('running','paused') AND deleted_at IS NULL
                         AND control_state IS DISTINCT FROM 'canceled' FOR UPDATE""",
                    UUID(task_id), UUID(run_id), delivery_id,
                )
                if task is None:
                    return
                run = await connection.fetchrow(
                    """SELECT id FROM public.automation_task_runs
                       WHERE id=$1::uuid AND task_id=$2::uuid
                         AND status IN ('running','paused') AND ended_at IS NULL FOR UPDATE""",
                    UUID(run_id), UUID(task_id),
                )
                if run is None:
                    return
                observed = datetime.now(timezone.utc)
                message = (
                    f"[{observed:%Y-%m-%d %H:%M:%S}: ERROR/Watchdog] "
                    f"{reason}; delivery={delivery_id}"
                )
                await connection.execute(
                    """INSERT INTO public.automation_task_logs
                       (task_id,run_id,level,message,created_at) VALUES ($1,$2,'warn',$3,$4)""",
                    UUID(task_id), UUID(run_id), message, observed,
                )
                await connection.execute(
                    """UPDATE public.automation_task_runs SET status='failed',ended_at=$2,
                       log_count=COALESCE(log_count,0)+1 WHERE id=$1""",
                    UUID(run_id), observed,
                )
                await connection.execute(
                    """UPDATE public.automation_tasks SET status='failed',throughput=0,
                       updated_at=$2 WHERE id=$1""", UUID(task_id), observed,
                )
    finally:
        if connection is not None:
            try:
                await asyncio.wait_for(connection.close(), timeout=2)
            except Exception:
                connection.terminate()


def _monitor(
    owner_pid: int, task_id: str, run_id: str, delivery_id: str, *,
    timeout_seconds: float = _HEARTBEAT_TIMEOUT, poll_seconds: float = 1.0,
    record_failure: FailureRecorder | None = None,
) -> None:
    condition = threading.Condition()
    state = {"last_activity": time.monotonic(), "done": False, "eof": False}

    def read_activity() -> None:
        try:
            for line in sys.stdin.buffer:
                with condition:
                    if line == b"DONE\n":
                        state["done"] = True
                        condition.notify()
                        return
                    if line == b"HEARTBEAT\n":
                        state["last_activity"] = time.monotonic()
                        condition.notify()
        finally:
            with condition:
                state["eof"] = True
                condition.notify()

    threading.Thread(target=read_activity, daemon=True).start()
    with condition:
        while not state["done"] and not state["eof"]:
            remaining = timeout_seconds - (time.monotonic() - state["last_activity"])
            if remaining <= 0:
                break
            condition.wait(timeout=min(poll_seconds, remaining))
        if state["done"]:
            return
        reason = "Worker exited without completing task cleanup"
        if not state["eof"]:
            reason = f"Crawl event loop or cleanup unresponsive for {timeout_seconds:g}s; worker terminated"
        # A reparented monitor must never kill a recycled, unrelated PID.
        # Unexpected pipe closure also stops a still-live owner before failing it.
        if os.getppid() == owner_pid:
            try:
                os.kill(owner_pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                logger.error("Watchdog could not terminate worker: task=%s error=%s",
                             task_id, type(exc).__name__)
                return
        elif not state["eof"]:
            reason = f"Crawl heartbeat absent for {timeout_seconds:g}s; original worker exited"
    try:
        asyncio.run((record_failure or _record_failure)(task_id, run_id, delivery_id, reason))
    except Exception as exc:
        # Connection exceptions can contain credentials; report the type only.
        logger.error("Watchdog failure write unconfirmed: task=%s delivery=%s error=%s",
                     task_id, delivery_id, type(exc).__name__)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _monitor(int(sys.argv[1]), *sys.argv[2:5])

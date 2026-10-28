"""Shared task-log formatting, ownership and real executor write paths.

Run with: python -m unittest discover -s tests -p test_task_logging.py -v
Persistence and worker I/O are mocked; these tests do not connect to services.
"""

import asyncio
import io
import json
import logging
import re
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.downloader.adapters import DownloadItem
from app.downloader.worker import DownloadWorker
from app.scheduler.task_log_capture import TaskLogCapture, WorkspaceTaskLogCapture
from app.syncer.worker import SyncWorker
from app.web.services.automation_task_store import AutomationTaskStore


EVENT_TIME = datetime(2026, 10, 28, 4, 5, 6, 123456, tzinfo=timezone.utc)
PREFIX = r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}: [A-Z]+/(Crawler|Downloader|Syncer)\] "


class MemoryTaskStore(AutomationTaskStore):
    def __init__(self):
        self.rows = []
        self.increment_run_stats = AsyncMock()
        self.on_append = None

    async def append_task_logs(self, task_id, logs):
        await asyncio.sleep(0)
        inserted = []
        for entry in logs:
            row = {**entry, "id": len(self.rows) + 1, "task_id": task_id}
            self.rows.append(row)
            inserted.append(row)
        if self.on_append:
            self.on_append()
        return inserted

    async def update_task_log_message(self, task_id, log_id, message, *, run_id=None):
        for row in self.rows:
            if row["id"] == log_id and row["task_id"] == task_id and row["run_id"] == run_id:
                row["message"] = message
                return True
        return False


def record(record_id="record-1", task_id="task-a", run_id="run-a"):
    return {"_meta": {
        "template": "collection", "data_type": "news", "record_id": record_id,
        "download_claim_token": "claim", "updated_at": "source-version", "download_version": 3,
        "search_params": {"__workspace_task_id": task_id, "__workspace_run_id": run_id},
    }}


def download_worker(*, completed=True, failure=None):
    worker = DownloadWorker.__new__(DownloadWorker)
    worker._collection_routes = {"collection": {"template_name": "site", "data_type": "news"}}
    worker._load_template = AsyncMock(return_value=SimpleNamespace(
        download=[], effective_download_use_proxy=False,
    ))
    worker._download_template_fields = Mock(return_value=[DownloadItem(
        url="https://example.test/file", filename="file.pdf", asset_key="assets.file",
    )])
    worker._existing_path = Mock(return_value=None)
    worker._download_item = AsyncMock(return_value="stored/file.pdf", side_effect=failure)
    worker._update_mongo = AsyncMock(return_value=completed)
    worker._mongo = SimpleNamespace(update_file_status=AsyncMock())
    return worker


def sync_worker(records):
    worker = SyncWorker.__new__(SyncWorker)
    worker._read_ready_records = AsyncMock(return_value=records)
    worker._semaphore = asyncio.Semaphore(10)
    worker._data_type = None
    worker._kafka = SimpleNamespace(send_record=AsyncMock())
    worker._mongo = SimpleNamespace(update_sync_status=AsyncMock(return_value=True))
    return worker


class TaskLogWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_event_has_identical_format_for_all_executors(self):
        store = MemoryTaskStore()
        for executor in ("Crawler", "Downloader", "Syncer"):
            await store.write_task_log(
                "task-a", "run-old", executor, logging.INFO, "processed %d / %s", 2, "三",
                created_at=EVENT_TIME,
            )
        for row in store.rows:
            self.assertEqual((row["task_id"], row["run_id"], row["level"]), ("task-a", "run-old", "info"))
            self.assertEqual(row["created_at"], EVENT_TIME)
            self.assertRegex(row["message"], PREFIX)
            self.assertTrue(row["message"].startswith("[2026-10-28 04:05:06,123: INFO/"))
            self.assertTrue(row["message"].endswith("] processed 2 / 三"))
            self.assertNotIn("[task=", row["message"])

    async def test_levels_and_event_times_keep_storage_and_display_semantics(self):
        store = MemoryTaskStore()
        for level, display, stored in (
            (logging.DEBUG, "DEBUG", "info"), ("info", "INFO", "info"),
            ("warn", "WARNING", "warn"), (logging.ERROR, "ERROR", "warn"),
            ("critical", "CRITICAL", "warn"), ("ok", "OK", "ok"),
        ):
            with self.subTest(level=level):
                await store.write_task_log("task", "run", "Crawler", level, "event", created_at=EVENT_TIME.timestamp())
                row = store.rows[-1]
                self.assertEqual(row["level"], stored)
                self.assertEqual(row["created_at"], EVENT_TIME)
                self.assertIn(f": {display}/Crawler]", row["message"])
        shifted = EVENT_TIME.astimezone(timezone(timedelta(hours=8)))
        await store.write_task_log("task", "run", "Syncer", "info", "shifted", created_at=shifted)
        self.assertTrue(store.rows[-1]["message"].startswith("[2026-10-28 04:05:06,123:"))
        await store.write_task_log("task", "run", "Syncer", "info", "naive", created_at=EVENT_TIME.replace(tzinfo=None))
        self.assertEqual(store.rows[-1]["created_at"], EVENT_TIME)

    async def test_background_logs_require_explicit_owner(self):
        store = MemoryTaskStore()
        for task_id, run_id in (("", "run"), ("task", "")):
            with self.assertRaises(ValueError):
                await store.write_task_log(task_id, run_id, "Downloader", "info", "event")
        with self.assertRaises(ValueError):
            await store.write_task_log("task", "run", "Downloader", "not-a-level", "event")
        self.assertEqual(store.rows, [])

    async def test_progress_rewrite_cannot_cross_task_or_run_and_falls_back_to_insert(self):
        store = MemoryTaskStore()
        await store.write_task_log("task", "old", "Crawler", "info", "download progress: 10%", created_at=EVENT_TIME)
        result = await store.write_task_log("task", "old", "Crawler", "info", "download progress: 20%", log_id=1)
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(store.rows[0]["created_at"], EVENT_TIME)
        await store.write_task_log("task", "new", "Crawler", "info", "download progress: new run", log_id=1)
        await store.write_task_log("other-task", "old", "Crawler", "info", "download progress: other task", log_id=1)
        self.assertEqual(len(store.rows), 3)
        self.assertTrue(store.rows[0]["message"].endswith("download progress: 20%"))

    async def test_raw_batch_and_single_log_api_remain_compatible(self):
        store = AutomationTaskStore.__new__(AutomationTaskStore)
        store.initialize = AsyncMock()
        logs = [
            {"run_id": "old", "level": "info", "message": "raw %s", "created_at": EVENT_TIME},
            {"run_id": "new", "level": "warn", "message": "raw second"},
        ]
        inserted = [{"id": 1, **logs[0]}, {"id": 2, **logs[1]}]
        store._pg = SimpleNamespace(fetch_all=AsyncMock(return_value=inserted))
        with patch("app.web.services.automation_task_store.publish_task_log", new_callable=AsyncMock) as publish:
            self.assertEqual(await store.append_task_logs("task", logs), inserted)
            self.assertEqual(store._pg.fetch_all.await_count, 1)
            self.assertEqual(publish.await_count, 2)
        sql, params = store._pg.fetch_all.await_args.args
        self.assertIn("run.task_id = task.id", sql)
        payload = json.loads(params["logs"])
        self.assertEqual([entry["message"] for entry in payload], ["raw %s", "raw second"])
        self.assertEqual([entry["run_id"] for entry in payload], ["old", "new"])
        store.append_task_logs = AsyncMock()
        await store.append_task_log("task", "ok", "legacy", run_id="run")
        store.append_task_logs.assert_awaited_once_with("task", [{"level": "ok", "message": "legacy", "run_id": "run"}])

    async def test_database_progress_update_scopes_task_and_run(self):
        store = AutomationTaskStore.__new__(AutomationTaskStore)
        store._pg = SimpleNamespace(fetch_one=AsyncMock(return_value=None))
        self.assertFalse(await store.update_task_log_message("task", 10, "line", run_id="old"))
        sql, params = store._pg.fetch_one.await_args.args
        self.assertIn("task_id = CAST(:task_id AS uuid)", sql)
        self.assertIn("run_id = CAST(:run_id AS uuid)", sql)
        self.assertEqual(params, {"task_id": "task", "run_id": "old", "log_id": 10, "message": "line"})


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.logger = logging.getLogger("task_logging_regression")
        self.original = (self.logger.handlers[:], self.logger.level, self.logger.propagate)
        handler = logging.StreamHandler(io.StringIO())
        handler.setFormatter(logging.Formatter("DIFFERENT-CONSOLE %(message)s"))
        self.logger.handlers = [handler]
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

    def tearDown(self):
        self.logger.handlers, level, self.logger.propagate = self.original
        self.logger.setLevel(level)

    async def test_crawler_preserves_event_time_and_traceback_without_console_format(self):
        store = MemoryTaskStore()
        capture = WorkspaceTaskLogCapture("task", "run", store)
        capture.start()
        try:
            event = logging.LogRecord(self.logger.name, logging.INFO, "", 0, "count=%d", (3,), None)
            event.created = EVENT_TIME.timestamp()
            self.logger.handle(event)
            try:
                raise ValueError("failure-detail")
            except ValueError:
                self.logger.exception("crawl failed")
        finally:
            await capture.stop()
        self.assertEqual(len(store.rows), 2)
        self.assertEqual(store.rows[0]["created_at"], EVENT_TIME)
        self.assertEqual(store.rows[0]["message"], "[2026-10-28 04:05:06,123: INFO/Crawler] count=3")
        self.assertEqual(store.rows[1]["level"], "warn")
        self.assertIn("ERROR/Crawler] crawl failed\nTraceback", store.rows[1]["message"])
        self.assertIn("ValueError: failure-detail", store.rows[1]["message"])
        self.assertNotIn("DIFFERENT-CONSOLE", store.rows[1]["message"])

    async def test_overlapping_crawlers_and_threads_do_not_duplicate_or_cross_runs(self):
        store = MemoryTaskStore()
        store.on_append = lambda: self.logger.warning("persistence-internal")
        ready = asyncio.Event()
        started = 0

        async def crawl(index, task_id, run_id):
            nonlocal started
            capture = WorkspaceTaskLogCapture(task_id, run_id, store)
            capture.start()
            started += 1
            if started == 4:
                ready.set()
            try:
                await ready.wait()
                self.logger.info("owner-%d event", index)
                await asyncio.to_thread(self.logger.info, "owner-%d thread", index)
            finally:
                await capture.stop()

        owners = [("task", "run-a"), ("task", "run-b"), ("other", "run-c"), ("task", "run-a")]
        await asyncio.gather(*(crawl(index, *owner) for index, owner in enumerate(owners)))
        self.assertEqual(len(store.rows), 8)
        for index, owner in enumerate(owners):
            rows = [row for row in store.rows if f"owner-{index} " in row["message"]]
            self.assertEqual(len(rows), 2)
            self.assertEqual({(row["task_id"], row["run_id"]) for row in rows}, {owner})
        self.assertFalse(any("persistence-internal" in row["message"] for row in store.rows))

    async def test_crawler_progress_keeps_one_row_and_automation_keeps_existing_console(self):
        store = MemoryTaskStore()
        capture = WorkspaceTaskLogCapture("task", "run", store)
        capture.start()
        try:
            for value in (10, 50, 100):
                self.logger.info("download progress: %d%%", value)
        finally:
            await capture.stop()
        self.assertEqual(len(store.rows), 1)
        self.assertTrue(store.rows[0]["message"].endswith("download progress: 100%"))
        legacy = TaskLogCapture("automation", "legacy-run", store)
        legacy.start()
        try:
            self.logger.info("legacy format")
        finally:
            await legacy.stop()
        self.assertEqual(store.rows[-1]["message"], "DIFFERENT-CONSOLE legacy format")

    async def test_crawler_writer_failure_does_not_recurse_or_stop_later_logs(self):
        store = MemoryTaskStore()
        write = store.write_task_log
        failed = False

        async def fail_once(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                self.logger.warning("persistence-error-internal")
                raise RuntimeError("log unavailable")
            return await write(*args, **kwargs)

        store.write_task_log = AsyncMock(side_effect=fail_once)
        errors = io.StringIO()
        capture = WorkspaceTaskLogCapture("task", "run", store)
        with patch("app.scheduler.task_log_capture.sys.stderr", errors):
            capture.start()
            try:
                self.logger.info("first event")
                self.logger.info("second event")
            finally:
                await capture.stop()
        self.assertEqual(store.write_task_log.await_count, 2)
        self.assertEqual(len(store.rows), 1)
        self.assertTrue(store.rows[0]["message"].endswith("second event"))
        self.assertIn("log unavailable", errors.getvalue())


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    setUp = CaptureTests.setUp
    tearDown = CaptureTests.tearDown

    async def test_syncer_records_only_acknowledged_owners_and_groups_by_original_run(self):
        store = MemoryTaskStore()
        inputs = [record("one"), record("two"), record("old", run_id="run-old"),
                  record("ownerless", run_id=""), record("stale"), record("failed")]
        worker = sync_worker(inputs)

        async def publish(item, **kwargs):
            if item["_meta"]["record_id"] == "failed":
                raise RuntimeError("Kafka rejected")

        async def acknowledge(template, data_type, record_id, status, **kwargs):
            return record_id != "stale"

        worker._kafka.send_record.side_effect = publish
        worker._mongo.update_sync_status.side_effect = acknowledge
        with patch("app.syncer.worker.ai_collect_store", store), patch("app.syncer.worker.logger"):
            self.assertEqual(await worker._process_batch(), 5)
        self.assertEqual(worker._kafka.send_record.await_count, 6)
        self.assertEqual(worker._mongo.update_sync_status.await_count, 5)
        self.assertEqual(store.increment_run_stats.await_count, 2)
        store.increment_run_stats.assert_any_await("task-a", "run-a", synced=2)
        store.increment_run_stats.assert_any_await("task-a", "run-old", synced=1)
        self.assertEqual(len(store.rows), 2)
        for row in store.rows:
            self.assertRegex(row["message"], PREFIX)
            self.assertIn("INFO/Syncer] Kafka publish acknowledged:", row["message"])
            self.assertNotIn("[task=", row["message"])
        for call in worker._mongo.update_sync_status.await_args_list:
            self.assertEqual(call.kwargs, {"updated_at": "source-version", "download_version": 3})

    async def test_downloader_success_failed_assets_and_stale_claim_keep_counter_conditions(self):
        for completed, failure, expected_level, expected_count in (
            (True, None, "info", 1), (True, RuntimeError("asset failed"), "info", 0),
            (False, None, "warn", 0),
        ):
            with self.subTest(completed=completed, failure=failure):
                store = MemoryTaskStore()
                worker = download_worker(completed=completed, failure=failure)
                with patch("app.downloader.worker.ai_collect_store", store), patch("app.downloader.worker.get_download_adapter", return_value=None), patch("app.downloader.worker.logger"):
                    await worker._download_record(record())
                self.assertEqual(len(store.rows), 1)
                self.assertEqual(store.rows[0]["level"], expected_level)
                self.assertEqual((store.rows[0]["task_id"], store.rows[0]["run_id"]), ("task-a", "run-a"))
                self.assertIn("/Downloader]", store.rows[0]["message"])
                self.assertEqual(store.increment_run_stats.await_count, expected_count)
                self.assertEqual(worker._update_mongo.await_args.args[3], "failed" if failure else "downloaded")
                worker._mongo.update_file_status.assert_not_awaited()

    async def test_downloader_no_assets_keeps_claim_transition_without_count_or_log(self):
        for missing_template in (False, True):
            with self.subTest(missing_template=missing_template):
                store = MemoryTaskStore()
                worker = download_worker()
                if missing_template:
                    worker._load_template.return_value = None
                else:
                    worker._download_template_fields.return_value = []
                with patch("app.downloader.worker.ai_collect_store", store), patch("app.downloader.worker.get_download_adapter", return_value=None):
                    await worker._download_record(record())
                worker._update_mongo.assert_awaited_once_with(
                    "collection", "record-1", {}, "no_assets", "claim",
                )
                worker._download_item.assert_not_awaited()
                store.increment_run_stats.assert_not_awaited()
                self.assertEqual(store.rows, [])

    async def test_downloader_log_failure_does_not_fail_completed_download(self):
        store = MemoryTaskStore()
        store.write_task_log = AsyncMock(side_effect=RuntimeError("log database unavailable"))
        worker = download_worker()
        with patch("app.downloader.worker.ai_collect_store", store), patch("app.downloader.worker.get_download_adapter", return_value=None), patch("app.downloader.worker.logger"):
            await worker._download_record(record())
        store.increment_run_stats.assert_awaited_once_with("task-a", "run-a", downloaded=1)
        self.assertEqual(worker._update_mongo.await_args.args[3], "downloaded")
        worker._mongo.update_file_status.assert_not_awaited()
        store.write_task_log.assert_awaited_once()

    async def test_downloader_unexpected_failure_keeps_failed_status_and_error_log(self):
        store = MemoryTaskStore()
        worker = download_worker()
        worker._load_template.side_effect = RuntimeError("template unavailable")
        with patch("app.downloader.worker.ai_collect_store", store), patch("app.downloader.worker.logger"):
            await worker._download_record(record())
        worker._mongo.update_file_status.assert_awaited_once_with(
            "collection", "record-1", "failed", claim_token="claim",
        )
        store.increment_run_stats.assert_not_awaited()
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(store.rows[0]["level"], "warn")
        self.assertIn("ERROR/Downloader]", store.rows[0]["message"])
        self.assertIn("template unavailable", store.rows[0]["message"])

    async def test_downloader_missing_owner_never_falls_back_to_current_run(self):
        for task_id, run_id in (("task", ""), ("", "run")):
            store = MemoryTaskStore()
            worker = download_worker()
            with patch("app.downloader.worker.ai_collect_store", store), patch("app.downloader.worker.get_download_adapter", return_value=None), patch("app.downloader.worker.logger"):
                await worker._download_record(record(task_id=task_id, run_id=run_id))
            self.assertEqual(store.rows, [])
            store.increment_run_stats.assert_not_awaited()

    async def test_three_executors_can_write_one_task_run_concurrently(self):
        store = MemoryTaskStore()
        downloader = download_worker()
        syncer = sync_worker([record()])

        async def crawl():
            capture = WorkspaceTaskLogCapture("task-a", "run-a", store)
            capture.start()
            try:
                await asyncio.sleep(0)
                self.logger.info("crawler concurrent event")
                await asyncio.sleep(0)
            finally:
                await capture.stop()

        with patch("app.downloader.worker.ai_collect_store", store), patch("app.downloader.worker.get_download_adapter", return_value=None), patch("app.downloader.worker.logger"), patch("app.syncer.worker.ai_collect_store", store), patch("app.syncer.worker.logger"):
            await asyncio.gather(crawl(), downloader._download_record(record()), syncer._process_batch())
        self.assertEqual(len(store.rows), 3)
        self.assertEqual({(row["task_id"], row["run_id"]) for row in store.rows}, {("task-a", "run-a")})
        self.assertEqual({re.search(r"/(\w+)\]", row["message"])[1] for row in store.rows}, {"Crawler", "Downloader", "Syncer"})
        for row in store.rows:
            self.assertRegex(row["message"], PREFIX)
        store.increment_run_stats.assert_any_await("task-a", "run-a", downloaded=1)
        store.increment_run_stats.assert_any_await("task-a", "run-a", synced=1)


if __name__ == "__main__":
    unittest.main()

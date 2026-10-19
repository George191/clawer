"""Shared automation task definitions, execution batches and logs."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.storage.minio_client import get_business_metadata_minio_client
from app.storage.postgres_client import get_pg_client
from app.utils.runtime_control import get_workspace_run
from app.web.services.task_events import publish_task_change, publish_task_log, publish_task_run


class AutomationTaskStore:
    def __init__(self) -> None:
        self._pg = get_pg_client()
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._initialize_lock:
            await self._pg.connect()

    async def list_tasks(self, product_domain: str | None = None) -> list[dict[str, Any]]:
        await self.initialize()
        rows = await self._pg.fetch_all(
            """
            SELECT task.*, '[]'::jsonb AS logs,
                   to_jsonb(run) AS current_run
            FROM public.automation_tasks task
            LEFT JOIN public.automation_task_runs run
              ON run.task_id = task.id AND run.id = task.current_run_id
            WHERE task.deleted_at IS NULL
              AND (CAST(:product_domain AS text) IS NULL OR task.product_domain = :product_domain)
            ORDER BY task.updated_at DESC
            """,
            {"product_domain": product_domain},
        )
        return await self._with_task_icons(rows)

    async def _with_task_icons(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = list({task["template_name"] for task in tasks if task.get("template_name")})
        if not names:
            return tasks
        templates = await self._pg.fetch_all(
            "SELECT name, version, data_type, icon FROM public.ai_collect_templates "
            "WHERE name = ANY(CAST(:names AS text[]))",
            {"names": names},
        )
        templates_by_version = {(row["name"], row["version"]): row for row in templates}
        minio = get_business_metadata_minio_client()
        for task in tasks:
            template = templates_by_version.get(
                (task.get("template_name"), task.get("template_version")), {},
            )
            task["data_type"] = template.get("data_type") or ""
            task["favicon_url"] = minio.build_object_url(str(template.get("icon") or ""))
        return tasks

    async def append_task_log(
        self, task_id: str, level: str, message: str, run_id: str | None = None,
    ) -> None:
        await self.append_task_logs(task_id, [{
            "level": level, "message": message, "run_id": run_id,
        }])

    async def append_task_logs(
        self,
        task_id: str,
        logs: list[dict[str, Any]],
    ) -> None:
        if not logs:
            return
        await self.initialize()
        inserted = await self._pg.fetch_all(
            """
            WITH inserted AS (
                INSERT INTO public.automation_task_logs
                    (task_id, run_id, level, message, created_at)
                SELECT task.id, run.id, entry.level, entry.message,
                       COALESCE(entry.created_at, now())
                FROM public.automation_tasks task
                CROSS JOIN jsonb_to_recordset(CAST(:logs AS jsonb))
                    AS entry(run_id uuid, level text, message text, created_at timestamptz)
                JOIN public.automation_task_runs run
                  ON run.id = COALESCE(entry.run_id, task.current_run_id)
                 AND run.task_id = task.id
                WHERE task.id = CAST(:task_id AS uuid)
                  AND task.deleted_at IS NULL
                RETURNING run_id, level, message, created_at
            ), counted AS (
                UPDATE public.automation_task_runs run
                SET log_count = run.log_count + totals.log_count
                FROM (SELECT run_id, count(*) AS log_count FROM inserted GROUP BY run_id) totals
                WHERE run.id = totals.run_id
                RETURNING run.id
            )
            SELECT run_id::text AS run_id, level, message, created_at FROM inserted
            """,
            {"task_id": task_id, "logs": json.dumps(logs, default=str, ensure_ascii=False)},
        )
        for log in inserted:
            await publish_task_log(task_id, log)

    async def increment_run_stats(
        self,
        task_id: str,
        run_id: str,
        *,
        inserted: int = 0,
        updated: int = 0,
        deleted: int = 0,
        downloaded: int = 0,
        synced: int = 0,
    ) -> None:
        await self.initialize()
        run = await self._pg.fetch_one(
            """
            UPDATE public.automation_task_runs SET
                inserted_records = COALESCE(inserted_records, 0) + :inserted,
                updated_records = COALESCE(updated_records, 0) + :updated,
                deleted_records = COALESCE(deleted_records, 0) + :deleted,
                downloaded_records = COALESCE(downloaded_records, 0) + :downloaded,
                synced_records = COALESCE(synced_records, 0) + :synced
            WHERE task_id = CAST(:task_id AS uuid) AND id = CAST(:run_id AS uuid)
              AND EXISTS (
                  SELECT 1 FROM public.automation_tasks task
                  WHERE task.id = CAST(:task_id AS uuid) AND task.deleted_at IS NULL
              )
            RETURNING *
            """,
            {
                "task_id": task_id,
                "run_id": run_id,
                "inserted": inserted,
                "updated": updated,
                "deleted": deleted,
                "downloaded": downloaded,
                "synced": synced,
            },
        )
        if run is not None:
            await publish_task_run(task_id, run)

    async def transition_task_stage_state(
        self,
        task_id: str,
        stage: str,
        current_state: str,
        next_state: str,
    ) -> dict[str, Any] | None:
        if stage not in {"download", "sync"}:
            raise ValueError(f"Unsupported task stage: {stage}")
        await self.initialize()
        state_column = f"{stage}_state"
        task = await self._pg.fetch_one(
            f"""
            UPDATE public.automation_tasks SET
                {state_column} = :next_state,
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
              AND deleted_at IS NULL
              AND {state_column} = :current_state
            RETURNING *
            """,
            {
                "id": task_id,
                "current_state": current_state,
                "next_state": next_state,
            },
        )
        if task:
            task["logs"] = []
            await publish_task_change(task_id)
        return task

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        policies = payload.get("policies", {})
        if payload.get("task_type", "template") == "template":
            policies = {key: value for key, value in policies.items()
                        if key not in {"watermark", "incremental_field"}}
        task = await self._pg.fetch_one(
            """
            INSERT INTO public.automation_tasks
                (name, product_domain, task_type, task_path,
                 template_name, template_version, status, download_state, sync_state,
                 schedule, parameters, policies, owner, started_at)
            VALUES
                (:name, :product_domain, :task_type, :task_path,
                 :template_name, :template_version, 'queued', :stage_state, :stage_state,
                 CAST(:schedule AS jsonb),
                 CAST(:parameters AS jsonb), CAST(:policies AS jsonb), :owner, NULL)
            RETURNING *
            """,
            {
                "product_domain": "ai-collect",
                "task_type": "template",
                "task_path": None,
                "template_name": None,
                "template_version": None,
                "owner": "System" if payload.get("task_type") == "celery" else "AI Collect",
                **payload,
                "stage_state": "paused" if payload.get("task_type") == "celery" else "running",
                "schedule": json.dumps(payload.get("schedule", {}), ensure_ascii=False),
                "parameters": json.dumps(payload.get("parameters", {}), ensure_ascii=False),
                "policies": json.dumps(policies, ensure_ascii=False),
            },
        )
        if task is None:
            raise RuntimeError("Task creation did not return a row")
        task["logs"] = []
        await publish_task_change(str(task["id"]))
        return task

    async def update_task(
        self, task_id: str, changes: dict[str, Any], *,
        expected_run_id: str | None = None,
        expected_celery_task_id: str | None = None,
    ) -> dict[str, Any] | None:
        current_run = get_workspace_run()
        if current_run is not None and current_run[0] == task_id:
            if expected_run_id is None:
                expected_run_id = current_run[1]
            if expected_celery_task_id is None:
                expected_celery_task_id = current_run[2]
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            WITH changed AS (
              UPDATE public.automation_tasks SET
                status = COALESCE(:status, status),
                progress = COALESCE(:progress, progress),
                records = COALESCE(:records, records),
                throughput = COALESCE(:throughput, throughput),
                control_state = COALESCE(:control_state, control_state),
                download_state = COALESCE(:download_state, download_state),
                sync_state = COALESCE(:sync_state, sync_state),
                started_at = CASE WHEN :start_now THEN now() ELSE started_at END,
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
              AND deleted_at IS NULL
              AND control_state IS DISTINCT FROM 'canceled'
              AND (CAST(:expected_run_id AS uuid) IS NULL
                   OR current_run_id = CAST(:expected_run_id AS uuid))
              AND (CAST(:expected_celery_task_id AS text) IS NULL
                   OR celery_task_id = :expected_celery_task_id)
              RETURNING *
            ), changed_run AS (
                UPDATE public.automation_task_runs run SET
                    status = CASE WHEN :status = 'queued' THEN 'completed' ELSE :status END,
                    ended_at = CASE WHEN :status IN ('queued', 'completed', 'failed')
                                    THEN now() ELSE NULL END
                FROM changed
                WHERE run.id = changed.current_run_id AND run.task_id = changed.id
                  AND :status IN ('running', 'paused', 'queued', 'completed', 'failed')
                  AND run.status IN ('running', 'paused')
                RETURNING run.id
            )
            SELECT * FROM changed
            """,
            {
                "id": task_id,
                "status": None,
                "progress": None,
                "records": None,
                "throughput": None,
                "control_state": None,
                "download_state": None,
                "sync_state": None,
                "start_now": False,
                **{key: value for key, value in changes.items() if key not in {
                    "inserted_records", "updated_records", "deleted_records",
                    "downloaded_records", "synced_records",
                }},
                "expected_run_id": expected_run_id,
                "expected_celery_task_id": expected_celery_task_id,
            },
        )
        if task:
            task["logs"] = []
            await publish_task_change(task_id)
        return task

    async def start_task(
        self,
        task_id: str,
        celery_task_id: str,
    ) -> dict[str, Any] | None:
        """Atomically claim a queued or failed task for execution."""
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            WITH current AS (
                SELECT id, current_run_id
                FROM public.automation_tasks
                WHERE id = CAST(:id AS uuid)
                  AND deleted_at IS NULL
                  AND status IN ('queued', 'failed')
                  AND control_state IS DISTINCT FROM 'canceled'
                FOR UPDATE
            ), closed AS (
                UPDATE public.automation_task_runs run
                SET status = 'canceled', ended_at = now()
                FROM current
                WHERE run.id = current.current_run_id AND run.status IN ('running', 'paused')
                RETURNING run.id
            ), new_run AS (
                INSERT INTO public.automation_task_runs (id, task_id)
                SELECT CAST(:celery_task_id AS uuid), id FROM current
                RETURNING id, task_id
            )
            UPDATE public.automation_tasks task SET
                status = 'running',
                celery_task_id = :celery_task_id,
                current_run_id = new_run.id,
                download_state = CASE WHEN task_type = 'template' THEN 'running' ELSE download_state END,
                sync_state = CASE WHEN task_type = 'template' THEN 'running' ELSE sync_state END,
                progress = 0,
                started_at = now(),
                updated_at = now()
            FROM new_run
            WHERE task.id = new_run.task_id
            RETURNING task.*
            """,
            {"id": task_id, "celery_task_id": celery_task_id},
        )
        if task:
            task["logs"] = []
            await publish_task_change(task_id)
        return task

    async def restart_task(
        self,
        task_id: str,
        celery_task_id: str,
    ) -> dict[str, Any] | None:
        """Atomically reset an existing task for a new execution."""
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            WITH current AS (
                SELECT id, celery_task_id, current_run_id
                FROM public.automation_tasks
                WHERE id = CAST(:id AS uuid)
                  AND deleted_at IS NULL
                  AND status IN ('running', 'paused', 'failed')
                  AND control_state IS DISTINCT FROM 'canceled'
                FOR UPDATE
            ), closed AS (
                UPDATE public.automation_task_runs run
                SET status = 'canceled', ended_at = now()
                FROM current
                WHERE run.id = current.current_run_id AND run.status IN ('running', 'paused')
                RETURNING run.id
            ), new_run AS (
                INSERT INTO public.automation_task_runs (id, task_id)
                SELECT CAST(:celery_task_id AS uuid), id FROM current
                RETURNING id, task_id
            )
            UPDATE public.automation_tasks task SET
                status = 'running',
                celery_task_id = :celery_task_id,
                current_run_id = new_run.id,
                progress = 0,
                records = 0,
                throughput = 0,
                control_state = NULL,
                download_state = CASE WHEN task_type = 'template' THEN 'running' ELSE download_state END,
                sync_state = CASE WHEN task_type = 'template' THEN 'running' ELSE sync_state END,
                started_at = now(),
                updated_at = now()
            FROM current JOIN new_run ON new_run.task_id = current.id
            WHERE task.id = current.id
            RETURNING task.*, current.celery_task_id AS previous_celery_task_id
            """,
            {"id": task_id, "celery_task_id": celery_task_id},
        )
        if task:
            task["logs"] = []
            await publish_task_change(task_id)
        return task

    async def claim_due_recurring_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        """Atomically claim due recurring tasks for one execution."""
        await self.initialize()
        tasks = await self._pg.fetch_all(
            """
            WITH due AS (
                SELECT id, gen_random_uuid() AS run_id
                FROM public.automation_tasks
                WHERE status IN ('queued', 'failed')
                  AND deleted_at IS NULL
                  AND control_state IS DISTINCT FROM 'canceled'
                  AND (
                    CASE
                      WHEN schedule->>'mode' = 'recurring'
                        THEN schedule->>'recurring_mode'
                      ELSE schedule->>'mode'
                    END
                  ) IN ('daily', 'interval')
                  AND (
                    (
                      (CASE WHEN schedule->>'mode' = 'recurring'
                         THEN schedule->>'recurring_mode' ELSE schedule->>'mode' END) = 'daily'
                      AND (now() AT TIME ZONE 'Asia/Shanghai')::time
                          >= COALESCE(NULLIF(schedule->>'daily_time', ''), '09:00')::time
                      AND (
                        started_at IS NULL
                        OR (started_at AT TIME ZONE 'Asia/Shanghai')::date
                           < (now() AT TIME ZONE 'Asia/Shanghai')::date
                      )
                    )
                    OR
                    (
                      (CASE WHEN schedule->>'mode' = 'recurring'
                         THEN schedule->>'recurring_mode' ELSE schedule->>'mode' END) = 'interval'
                      AND (
                        started_at IS NULL
                        OR started_at + (
                          COALESCE(NULLIF(schedule->>'interval_value', '')::integer, 30)
                          * CASE WHEN schedule->>'interval_unit' = 'hour' THEN 3600 ELSE 60 END
                          * interval '1 second'
                        ) <= now()
                      )
                    )
                  )
                ORDER BY COALESCE(started_at, created_at), created_at
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            ), new_runs AS (
                INSERT INTO public.automation_task_runs (id, task_id)
                SELECT run_id, id FROM due
                RETURNING id, task_id
            )
            UPDATE public.automation_tasks task SET
                status = 'running',
                celery_task_id = new_runs.id::text,
                current_run_id = new_runs.id,
                progress = 0,
                download_state = CASE
                    WHEN task.download_state = 'paused' THEN 'paused' ELSE 'running' END,
                sync_state = CASE
                    WHEN task.sync_state IN ('paused', 'canceled') THEN task.sync_state ELSE 'running' END,
                started_at = now(),
                updated_at = now()
            FROM new_runs
            WHERE task.id = new_runs.task_id
            RETURNING task.*
            """,
            {"limit": limit},
        )
        for task in tasks:
            task["logs"] = []
            await publish_task_change(str(task["id"]))
        return tasks

    async def set_active_task_status(
        self,
        task_id: str,
        current_status: str,
        next_status: str,
        celery_task_id: str | None = None,
        expected_celery_task_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Transition an active task only from its expected current state.

        当传入 celery_task_id 时一并更新（resume 续跑需要新的 Celery 任务 id）。
        """
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            WITH changed AS (
              UPDATE public.automation_tasks SET
                status = :next_status,
                celery_task_id = COALESCE(:celery_task_id, celery_task_id),
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
              AND deleted_at IS NULL
              AND status = :current_status
              AND control_state IS DISTINCT FROM 'canceled'
              AND (CAST(:expected_celery_task_id AS text) IS NULL
                   OR celery_task_id = :expected_celery_task_id)
              RETURNING *
            ), changed_run AS (
                UPDATE public.automation_task_runs run SET
                    status = :next_status,
                    ended_at = CASE WHEN :next_status IN ('completed', 'failed')
                                    THEN now() ELSE NULL END
                FROM changed
                WHERE run.id = changed.current_run_id AND run.task_id = changed.id
                  AND run.status IN ('running', 'paused')
                RETURNING run.id
            )
            SELECT * FROM changed
            """,
            {
                "id": task_id,
                "current_status": current_status,
                "next_status": next_status,
                "celery_task_id": celery_task_id,
                "expected_celery_task_id": expected_celery_task_id,
            },
        )
        if task:
            task["logs"] = []
            await publish_task_change(task_id)
        return task

    async def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            WITH changed AS (
              UPDATE public.automation_tasks SET
                status = 'failed', control_state = 'canceled', throughput = 0,
                download_state = 'paused', sync_state = 'canceled', updated_at = now()
            WHERE id = CAST(:id AS uuid) AND deleted_at IS NULL
              AND status IN ('queued', 'running', 'paused', 'failed')
              AND control_state IS DISTINCT FROM 'canceled'
              RETURNING *
            ), changed_run AS (
                UPDATE public.automation_task_runs run SET status = 'canceled', ended_at = now()
                FROM changed
                WHERE run.id = changed.current_run_id AND run.task_id = changed.id
                  AND run.status IN ('running', 'paused')
                RETURNING run.id
            )
            SELECT * FROM changed
            """,
            {"id": task_id},
        )
        if task:
            task["logs"] = []
            await publish_task_change(task_id)
        return task

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            SELECT task.*, to_jsonb(current_run) AS current_run,
                   COALESCE((
                       SELECT jsonb_agg(log_row.payload ORDER BY log_row.created_at)
                       FROM (
                           SELECT created_at,
                                  jsonb_build_object(
                                      'level', level,
                                      'message', message,
                                      'created_at', created_at,
                                      'run_id', run_id::text
                                  ) AS payload
                           FROM public.automation_task_logs
                           WHERE task_id = task.id AND run_id = current_run.id
                           ORDER BY created_at DESC
                           LIMIT 200
                       ) log_row
                   ), '[]'::jsonb) AS logs,
                   COALESCE((
                       SELECT jsonb_agg(
                           to_jsonb(run_row) ORDER BY run_row.started_at DESC, run_row.id DESC
                       )
                       FROM (
                           SELECT * FROM public.automation_task_runs
                           WHERE task_id = task.id
                           ORDER BY started_at DESC, id DESC
                           LIMIT 20
                       ) run_row
                   ), '[]'::jsonb) AS log_runs,
                   (SELECT count(*) FROM public.automation_task_runs
                    WHERE task_id = task.id)::integer AS log_run_count
            FROM public.automation_tasks task
            LEFT JOIN public.automation_task_runs current_run
              ON current_run.task_id = task.id
             AND current_run.id = COALESCE(task.current_run_id, (
                 SELECT latest.id FROM public.automation_task_runs latest
                 WHERE latest.task_id = task.id
                 ORDER BY latest.started_at DESC, latest.id DESC LIMIT 1
             ))
            WHERE task.id = CAST(:id AS uuid)
              AND task.deleted_at IS NULL
            """,
            {"id": task_id},
        )
        return (await self._with_task_icons([task]))[0] if task is not None else None

    async def cleanup_task_logs(self) -> dict[str, int]:
        """Delete expired terminal runs; foreign keys remove logs and clear run references."""
        await self.initialize()
        result = await self._pg.fetch_one(
            """
            WITH deleted AS (
                DELETE FROM public.automation_task_runs
                WHERE status IN ('completed', 'failed', 'canceled', 'legacy')
                  AND COALESCE(ended_at, started_at) < now() - interval '1 month'
                RETURNING id
            )
            SELECT count(*) AS deleted_runs FROM deleted
            """
        )
        return {"deleted_runs": result["deleted_runs"]}

    async def get_task_logs(self, task_id: str, run_id: str) -> list[dict[str, Any]]:
        await self.initialize()
        return await self._pg.fetch_all(
            """
            SELECT level, message, created_at, run_id::text AS run_id
            FROM (
                SELECT log.level, log.message, log.created_at, log.run_id
                FROM public.automation_task_logs log
                JOIN public.automation_tasks task ON task.id = log.task_id
                WHERE log.task_id = CAST(:task_id AS uuid)
                  AND log.run_id = CAST(:run_id AS uuid)
                  AND task.deleted_at IS NULL
                ORDER BY log.created_at DESC
                LIMIT 200
            ) run_logs
            ORDER BY created_at
            """,
            {"task_id": task_id, "run_id": run_id},
        )

    async def get_task_log_runs(
        self,
        task_id: str,
        offset: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        return await self._pg.fetch_all(
            """
            SELECT run.*
            FROM public.automation_task_runs run
            JOIN public.automation_tasks task ON task.id = run.task_id
            WHERE run.task_id = CAST(:task_id AS uuid)
              AND task.deleted_at IS NULL
            ORDER BY run.started_at DESC, run.id DESC
            OFFSET :offset
            LIMIT :limit
            """,
            {"task_id": task_id, "offset": offset, "limit": limit},
        )

    async def get_task_run(self, task_id: str, run_id: str) -> dict[str, Any] | None:
        await self.initialize()
        return await self._pg.fetch_one(
            """
            SELECT run.* FROM public.automation_task_runs run
            JOIN public.automation_tasks task ON task.id = run.task_id
            WHERE run.task_id = CAST(:task_id AS uuid) AND run.id = CAST(:run_id AS uuid)
              AND task.deleted_at IS NULL
            """,
            {"task_id": task_id, "run_id": run_id},
        )

    async def get_task_for_execution(self, task_id: str) -> dict[str, Any] | None:
        """Load execution configuration without aggregating UI task logs."""
        await self.initialize()
        return await self._pg.fetch_one(
            """
            SELECT *
            FROM public.automation_tasks
            WHERE id = CAST(:id AS uuid)
              AND deleted_at IS NULL
            """,
            {"id": task_id},
        )

    async def get_task_control(self, task_id: str) -> dict[str, Any] | None:
        await self.initialize()
        return await self._pg.fetch_one(
            """
            SELECT status, control_state, download_state, sync_state, celery_task_id, current_run_id,
                   task_type, task_path, product_domain
            FROM public.automation_tasks
            WHERE id = CAST(:id AS uuid)
              AND deleted_at IS NULL
            """,
            {"id": task_id},
        )

    async def delete_task(self, task_id: str) -> bool:
        """Soft-delete an unstarted or terminal task; active tasks must be canceled first."""
        await self.initialize()
        deleted = await self._pg.fetch_one(
            """
            UPDATE public.automation_tasks SET
                deleted_at = now(),
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
              AND deleted_at IS NULL
              AND status NOT IN ('running', 'paused')
            RETURNING id
            """,
            {"id": task_id},
        )
        if deleted is not None:
            await publish_task_change(task_id)
        return deleted is not None


automation_task_store = AutomationTaskStore()

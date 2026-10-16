-- Run with old web/worker processes stopped; the migration is transactional.
-- Historical change counters cannot be reconstructed from console logs.

LOCK TABLE public.ai_collect_tasks IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.ai_collect_task_logs IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE IF NOT EXISTS public.ai_collect_task_runs (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES public.ai_collect_tasks(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'paused', 'completed', 'failed', 'canceled', 'legacy')),
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    log_count bigint NOT NULL DEFAULT 0 CHECK (log_count >= 0),
    inserted_records bigint DEFAULT 0 CHECK (inserted_records >= 0),
    updated_records bigint DEFAULT 0 CHECK (updated_records >= 0),
    deleted_records bigint DEFAULT 0 CHECK (deleted_records >= 0),
    downloaded_records bigint DEFAULT 0 CHECK (downloaded_records >= 0),
    synced_records bigint DEFAULT 0 CHECK (synced_records >= 0),
    UNIQUE (task_id, id)
);

CREATE INDEX IF NOT EXISTS idx_ai_collect_task_runs_task_started
    ON public.ai_collect_task_runs (task_id, started_at DESC, id DESC);

ALTER TABLE public.ai_collect_tasks ADD COLUMN IF NOT EXISTS current_run_id uuid;

CREATE TABLE IF NOT EXISTS public.ai_collect_task_stats_legacy (
    task_id uuid PRIMARY KEY,
    captured_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    inserted_records bigint,
    updated_records bigint,
    deleted_records bigint,
    downloaded_records bigint,
    synced_records bigint
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ai_collect_tasks'
          AND column_name = 'inserted_records'
    ) THEN
        INSERT INTO public.ai_collect_task_stats_legacy (
            task_id, started_at, inserted_records, updated_records,
            deleted_records, downloaded_records, synced_records
        )
        SELECT id, started_at, inserted_records, updated_records,
               deleted_records, downloaded_records, synced_records
        FROM public.ai_collect_tasks
        ON CONFLICT (task_id) DO NOTHING;
    END IF;
END
$$;

-- Keep formerly ungrouped scheduler/system logs in an explicitly legacy batch.
UPDATE public.ai_collect_task_logs
SET run_id = md5(task_id::text || ':unassigned-logs')::uuid
WHERE run_id IS NULL;

INSERT INTO public.ai_collect_task_runs (
    id, task_id, status, started_at, log_count,
    inserted_records, updated_records, deleted_records,
    downloaded_records, synced_records
)
SELECT run_id, task_id, 'legacy', min(created_at), count(*),
       NULL, NULL, NULL, NULL, NULL
FROM public.ai_collect_task_logs
GROUP BY task_id, run_id
ON CONFLICT (id) DO UPDATE SET log_count = EXCLUDED.log_count;

-- A just-dispatched or paused execution may not have persisted its first log.
INSERT INTO public.ai_collect_task_runs (
    id, task_id, status, started_at, log_count,
    inserted_records, updated_records, deleted_records,
    downloaded_records, synced_records
)
SELECT md5(task.id::text || ':active-execution')::uuid, task.id, task.status,
       COALESCE(task.started_at, task.updated_at, task.created_at), 0,
       NULL, NULL, NULL, NULL, NULL
FROM public.ai_collect_tasks task
WHERE task.status IN ('running', 'paused')
  AND NOT EXISTS (
      SELECT 1 FROM public.ai_collect_task_runs run
      WHERE run.task_id = task.id
        AND (task.started_at IS NULL OR run.started_at >= task.started_at)
        AND run.id <> md5(task.id::text || ':unassigned-logs')::uuid
  )
ON CONFLICT (id) DO NOTHING;

UPDATE public.ai_collect_tasks task
SET current_run_id = (
    SELECT run.id
    FROM public.ai_collect_task_runs run
    WHERE run.task_id = task.id
    ORDER BY (run.id = md5(task.id::text || ':unassigned-logs')::uuid),
             run.started_at DESC, run.id DESC
    LIMIT 1
)
WHERE task.current_run_id IS NULL;

-- Only an active task establishes the state of its migrated current batch.
UPDATE public.ai_collect_task_runs run
SET status = task.status
FROM public.ai_collect_tasks task
WHERE run.id = task.current_run_id AND run.status = 'legacy'
  AND task.status IN ('running', 'paused');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.ai_collect_tasks'::regclass
          AND conname = 'fk_ai_collect_tasks_current_run'
    ) THEN
        ALTER TABLE public.ai_collect_tasks
            ADD CONSTRAINT fk_ai_collect_tasks_current_run
            FOREIGN KEY (current_run_id) REFERENCES public.ai_collect_task_runs(id)
            ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.ai_collect_task_logs'::regclass
          AND conname = 'fk_ai_collect_task_logs_run'
    ) THEN
        ALTER TABLE public.ai_collect_task_logs
            ADD CONSTRAINT fk_ai_collect_task_logs_run
            FOREIGN KEY (task_id, run_id)
            REFERENCES public.ai_collect_task_runs(task_id, id)
            ON DELETE CASCADE NOT VALID;
    END IF;
END
$$;

ALTER TABLE public.ai_collect_task_logs VALIDATE CONSTRAINT fk_ai_collect_task_logs_run;
ALTER TABLE public.ai_collect_task_logs ALTER COLUMN run_id SET NOT NULL;

ALTER TABLE public.ai_collect_tasks
    DROP COLUMN IF EXISTS inserted_records,
    DROP COLUMN IF EXISTS updated_records,
    DROP COLUMN IF EXISTS deleted_records,
    DROP COLUMN IF EXISTS downloaded_records,
    DROP COLUMN IF EXISTS synced_records;

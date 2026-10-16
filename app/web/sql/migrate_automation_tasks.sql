-- Stop all task writers and run migrate_task_runs before migrating an existing database.
-- Execute this file in one transaction (app.web.migrate_automation_tasks does so).

DO $$
DECLARE
    suffix text;
    old_table regclass;
    new_table regclass;
BEGIN
    FOREACH suffix IN ARRAY ARRAY['tasks', 'task_runs', 'task_logs', 'task_stats_legacy']
    LOOP
        old_table := to_regclass('public.ai_collect_' || suffix);
        new_table := to_regclass('public.automation_' || suffix);
        IF old_table IS NOT NULL AND new_table IS NOT NULL THEN
            RAISE EXCEPTION 'Both ai_collect_% and automation_% exist; reconcile them before migrating',
                suffix, suffix;
        END IF;
    END LOOP;

    IF to_regclass('public.ai_collect_tasks') IS NOT NULL AND (
        to_regclass('public.ai_collect_task_runs') IS NULL
        OR to_regclass('public.ai_collect_task_logs') IS NULL
    ) THEN
        RAISE EXCEPTION 'Run app.web.migrate_task_runs --apply before migrating existing tasks';
    END IF;
    IF to_regclass('public.ai_collect_tasks') IS NULL AND (
        to_regclass('public.ai_collect_task_runs') IS NOT NULL
        OR to_regclass('public.ai_collect_task_logs') IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Incomplete ai_collect task schema; restore the task table before migrating';
    END IF;

    FOREACH suffix IN ARRAY ARRAY['tasks', 'task_runs', 'task_logs', 'task_stats_legacy']
    LOOP
        old_table := to_regclass('public.ai_collect_' || suffix);
        new_table := to_regclass('public.automation_' || suffix);
        IF old_table IS NOT NULL THEN
            EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', 'ai_collect_' || suffix);
            EXECUTE format('ALTER TABLE public.%I RENAME TO %I',
                'ai_collect_' || suffix, 'automation_' || suffix);
        ELSIF new_table IS NOT NULL THEN
            EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', 'automation_' || suffix);
        END IF;
    END LOOP;
END
$$;

-- A new database can initialize the shared execution tables without a collection template.
CREATE TABLE IF NOT EXISTS public.automation_tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    product_domain text NOT NULL DEFAULT 'ai-collect',
    task_type text NOT NULL DEFAULT 'template',
    task_path text,
    template_name text,
    template_version text DEFAULT 'v1.0',
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'paused', 'canceled')),
    progress integer NOT NULL DEFAULT 0,
    records bigint NOT NULL DEFAULT 0,
    throughput integer NOT NULL DEFAULT 0,
    celery_task_id text,
    control_state text,
    download_state text NOT NULL DEFAULT 'running',
    sync_state text NOT NULL DEFAULT 'running',
    schedule jsonb NOT NULL DEFAULT '{}'::jsonb,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    policies jsonb NOT NULL DEFAULT '{}'::jsonb,
    owner text NOT NULL DEFAULT 'AI Collect',
    current_run_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    deleted_at timestamptz
);

ALTER TABLE public.automation_tasks
    ADD COLUMN IF NOT EXISTS product_domain text NOT NULL DEFAULT 'ai-collect',
    ADD COLUMN IF NOT EXISTS task_type text NOT NULL DEFAULT 'template',
    ADD COLUMN IF NOT EXISTS task_path text;
ALTER TABLE public.automation_tasks
    ALTER COLUMN template_name DROP NOT NULL,
    ALTER COLUMN template_version DROP NOT NULL;

CREATE TABLE IF NOT EXISTS public.automation_task_runs (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES public.automation_tasks(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS public.automation_task_logs (
    id bigserial PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES public.automation_tasks(id) ON DELETE CASCADE,
    run_id uuid NOT NULL,
    level text NOT NULL DEFAULT 'info',
    message text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (task_id, run_id)
        REFERENCES public.automation_task_runs(task_id, id) ON DELETE CASCADE
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.automation_tasks'::regclass
          AND conname = 'automation_tasks_type_check'
    ) THEN
        ALTER TABLE public.automation_tasks ADD CONSTRAINT automation_tasks_type_check
            CHECK (task_type IN ('template', 'celery'));
    END IF;
    -- Renaming a table preserves its existing foreign keys, including their old names.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.automation_tasks'::regclass
          AND confrelid = 'public.automation_task_runs'::regclass
          AND contype = 'f'
          AND conkey = ARRAY[(
              SELECT attnum FROM pg_attribute
              WHERE attrelid = 'public.automation_tasks'::regclass
                AND attname = 'current_run_id'
          )]::smallint[]
    ) THEN
        ALTER TABLE public.automation_tasks ADD CONSTRAINT fk_automation_tasks_current_run
            FOREIGN KEY (current_run_id) REFERENCES public.automation_task_runs(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

-- Existing indexes keep their names after table renames; avoid duplicate indexes on upgrades.
DO $$
BEGIN
    IF to_regclass('public.idx_ai_collect_tasks_status') IS NULL THEN
        CREATE INDEX IF NOT EXISTS idx_automation_tasks_status
            ON public.automation_tasks(status, updated_at DESC);
    END IF;
    IF to_regclass('public.idx_ai_collect_task_runs_task_started') IS NULL THEN
        CREATE INDEX IF NOT EXISTS idx_automation_task_runs_task_started
            ON public.automation_task_runs(task_id, started_at DESC, id DESC);
    END IF;
    IF to_regclass('public.idx_ai_collect_task_logs_task') IS NULL THEN
        CREATE INDEX IF NOT EXISTS idx_automation_task_logs_task
            ON public.automation_task_logs(task_id, created_at DESC);
    END IF;
    IF to_regclass('public.idx_ai_collect_task_logs_run') IS NULL THEN
        CREATE INDEX IF NOT EXISTS idx_automation_task_logs_run
            ON public.automation_task_logs(task_id, run_id, created_at DESC);
    END IF;
END
$$;

INSERT INTO public.automation_tasks (
    id, name, product_domain, task_type, task_path, owner, status,
    download_state, sync_state, schedule
)
VALUES (
    md5('automation:task_log_cleanup')::uuid,
    '系统任务日志清理', 'platform', 'celery',
    'app.scheduler.tasks.automation.cleanup_task_logs', 'System', 'queued',
    'paused', 'paused', '{"mode":"daily","daily_time":"03:00"}'::jsonb
)
ON CONFLICT (id) DO NOTHING;

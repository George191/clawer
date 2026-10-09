CREATE TABLE IF NOT EXISTS public.scheduler_tasks (
    id BIGSERIAL PRIMARY KEY,
    task_name VARCHAR(255) NOT NULL UNIQUE,
    task_path VARCHAR(500) NOT NULL,
    product_domain VARCHAR(50) NOT NULL DEFAULT 'platform',
    description TEXT,
    schedule_type VARCHAR(20) NOT NULL DEFAULT 'crontab',
    cron_minute VARCHAR(50) NOT NULL DEFAULT '*',
    cron_hour VARCHAR(50) NOT NULL DEFAULT '*',
    cron_day_of_week VARCHAR(50) NOT NULL DEFAULT '*',
    cron_day_of_month VARCHAR(50) NOT NULL DEFAULT '*',
    cron_month_of_year VARCHAR(50) NOT NULL DEFAULT '*',
    interval_seconds INTEGER,
    args JSONB NOT NULL DEFAULT '[]'::jsonb,
    kwargs JSONB NOT NULL DEFAULT '{}'::jsonb,
    options JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT scheduler_tasks_domain_check CHECK (
        product_domain IN ('ai-collect', 'data-lake', 'etl-pipeline', 'data-cockpit',
                           'knowledge-graph', 'knowledge-rag', 'platform')
    ),
    CONSTRAINT scheduler_tasks_type_check CHECK (schedule_type IN ('crontab', 'interval'))
);

ALTER TABLE public.scheduler_tasks
    ADD COLUMN IF NOT EXISTS product_domain VARCHAR(50) NOT NULL DEFAULT 'platform';

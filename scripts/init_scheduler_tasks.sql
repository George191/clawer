-- 调度任务配置表 — 数据驱动的任务调度配置。
--
-- 用户可通过 Web API 维护此表，Beat 调度器启动时从表中加载启用的任务。
-- 表位于 public schema，与业务分层 schema (ts_*) 隔离。
--
-- 相关代码:
--   app/scheduler/task_repository.py  — 数据访问层
--   app/scheduler/beat_schedule.py    — BeatScheduleRegistry 加载逻辑
--   app/web/routes/scheduler.py       — Web API

CREATE TABLE IF NOT EXISTS public.scheduler_tasks (
    id BIGSERIAL PRIMARY KEY,

    -- 任务标识
    task_name TEXT NOT NULL UNIQUE,          -- 任务名（= beat_schedule key，与 TaskStore 对齐）
    task_path TEXT NOT NULL,                  -- Celery task 路径，如 app.scheduler.tasks.google_patent.crawl_daily
    description TEXT,                         -- 任务描述（用户可读）

    -- 调度类型与配置
    schedule_type TEXT NOT NULL DEFAULT 'crontab',  -- crontab / interval

    -- crontab 字段（schedule_type='crontab' 时生效）
    cron_minute TEXT NOT NULL DEFAULT '*',
    cron_hour TEXT NOT NULL DEFAULT '*',
    cron_day_of_week TEXT NOT NULL DEFAULT '*',
    cron_day_of_month TEXT NOT NULL DEFAULT '*',
    cron_month_of_year TEXT NOT NULL DEFAULT '*',

    -- interval 字段（schedule_type='interval' 时生效）
    interval_seconds INT,

    -- 任务参数
    args JSONB NOT NULL DEFAULT '[]'::jsonb,
    kwargs JSONB NOT NULL DEFAULT '{}'::jsonb,
    options JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- 状态
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    -- 审计
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT,

    CONSTRAINT scheduler_tasks_schedule_type_check
        CHECK (schedule_type IN ('crontab', 'interval')),
    CONSTRAINT scheduler_tasks_interval_check
        CHECK (
            schedule_type != 'interval'
            OR (interval_seconds IS NOT NULL AND interval_seconds > 0)
        )
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_scheduler_tasks_enabled
    ON public.scheduler_tasks (enabled);

CREATE INDEX IF NOT EXISTS idx_scheduler_tasks_updated_at
    ON public.scheduler_tasks (updated_at DESC);

-- updated_at 自动更新触发器
CREATE OR REPLACE FUNCTION public.scheduler_tasks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_scheduler_tasks_updated_at ON public.scheduler_tasks;
CREATE TRIGGER trg_scheduler_tasks_updated_at
    BEFORE UPDATE ON public.scheduler_tasks
    FOR EACH ROW
    EXECUTE FUNCTION public.scheduler_tasks_updated_at();

-- ── 种子数据：内置默认任务 ──────────────────────────────────────────
-- 与原 beat_schedule.py 中的硬编码配置一致，确保平滑迁移
INSERT INTO public.scheduler_tasks (
    task_name, task_path, description,
    schedule_type,
    cron_minute, cron_hour, cron_day_of_week, cron_day_of_month, cron_month_of_year,
    kwargs, options, enabled
) VALUES
(
    'google_patent_daily',
    'app.scheduler.tasks.google_patent.crawl_daily',
    'Google Patent 每日采集（UTC 06:00 执行，采集前一天发布的专利）',
    'crontab',
    '0', '6', '*', '*', '*',
    '{}'::jsonb,
    '{"queue": "patent", "expires": 21600}'::jsonb,
    TRUE
),
(
    'google_patent_range',
    'app.scheduler.tasks.google_patent.crawl_date_range',
    'Google Patent 每周全量补采（周一 UTC 03:00，采集过去 7 天）',
    'crontab',
    '0', '3', '1', '*', '*',
    '{"days_back": 7}'::jsonb,
    '{"queue": "patent", "expires": 43200}'::jsonb,
    TRUE
)
ON CONFLICT (task_name) DO NOTHING;

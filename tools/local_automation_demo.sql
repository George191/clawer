-- Local visual QA fixtures. Run only against clawer-local-test-postgres-1:
-- docker exec -e PGAPPNAME=clawer-local-demo -i clawer-local-test-postgres-1 ... psql ...
-- All records are synthetic; no worker execution is represented by these fixtures.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'www.astral.io' OR current_setting('application_name') <> 'clawer-local-demo' THEN
    RAISE EXCEPTION 'This fixture requires the explicitly selected local demo database session';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS app.automation_tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL, product_domain text NOT NULL DEFAULT 'platform',
  task_type text NOT NULL DEFAULT 'template', task_path text,
  template_name text, template_version text,
  status text NOT NULL DEFAULT 'queued', progress integer NOT NULL DEFAULT 0,
  records bigint NOT NULL DEFAULT 0, throughput double precision NOT NULL DEFAULT 0,
  control_state text, download_state text NOT NULL DEFAULT 'idle', sync_state text NOT NULL DEFAULT 'idle',
  schedule jsonb NOT NULL DEFAULT '{}', parameters jsonb NOT NULL DEFAULT '{}', policies jsonb NOT NULL DEFAULT '{}',
  owner text NOT NULL, celery_task_id text, current_run_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz, deleted_at timestamptz
);
CREATE TABLE IF NOT EXISTS app.automation_task_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task_id uuid NOT NULL REFERENCES app.automation_tasks(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'running', started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz,
  log_count bigint NOT NULL DEFAULT 0, inserted_records bigint, updated_records bigint, deleted_records bigint,
  downloaded_records bigint, synced_records bigint
);
CREATE TABLE IF NOT EXISTS app.automation_task_logs (
  id bigserial PRIMARY KEY, task_id uuid NOT NULL REFERENCES app.automation_tasks(id) ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES app.automation_task_runs(id) ON DELETE CASCADE,
  level text NOT NULL, message text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS automation_task_logs_run_order ON app.automation_task_logs(task_id,run_id,created_at,id);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.automation_tasks'::regclass AND conname='automation_tasks_current_run_fk') THEN
    ALTER TABLE app.automation_tasks ADD CONSTRAINT automation_tasks_current_run_fk FOREIGN KEY(current_run_id)
      REFERENCES app.automation_task_runs(id) ON DELETE SET NULL;
  END IF;
END $$;

-- Use the scheduler's existing table definition, installed separately before this fixture.
INSERT INTO app.automation_workflows(name,product_domain,description,nodes,enabled,updated_at)
VALUES
 ('Demo · Patent enrichment','ai-collect','Synthetic preview: collect patent records, normalize fields, validate and publish.',
  '[{"name":"Collect patents","type":"collect","template":"Patent source"},{"name":"Normalize fields","type":"transform"},{"name":"Validate identifiers","type":"validate"},{"name":"Publish to warehouse","type":"publish"}]',true,now()-interval '12 minutes'),
 ('Demo · Company reconciliation','etl-pipeline','Synthetic preview of a governed company identity pipeline.',
  '[{"name":"Read company records","type":"collect"},{"name":"Resolve company identity","type":"transform"},{"name":"Check duplicates","type":"validate"},{"name":"Publish company objects","type":"publish"}]',true,now()-interval '45 minutes'),
 ('Demo · Media quality review','data-lake','Synthetic preview: inspect resource metadata before publishing.',
  '[{"name":"Inspect resource metadata","type":"task"},{"name":"Validate media types","type":"validate"},{"name":"Publish approved resources","type":"publish"}]',false,now()-interval '2 hours'),
 ('Demo · Industry metrics','data-cockpit','Synthetic preview of a daily reporting definition.',
  '[{"name":"Read warehouse metrics","type":"collect"},{"name":"Aggregate by industry","type":"transform"},{"name":"Publish report","type":"publish"}]',true,now()-interval '3 hours'),
 ('Demo · Expert graph refresh','knowledge-graph','Synthetic preview of expert relationship updates.',
  '[{"name":"Read expert profiles","type":"collect"},{"name":"Resolve relationships","type":"transform"},{"name":"Validate graph edges","type":"validate"}]',false,now()-interval '1 day')
ON CONFLICT(name) DO UPDATE SET description=excluded.description,nodes=excluded.nodes,enabled=excluded.enabled,updated_at=excluded.updated_at;

INSERT INTO app.scheduler_tasks(task_name,task_path,product_domain,description,schedule_type,cron_minute,cron_hour,cron_day_of_week,cron_day_of_month,cron_month_of_year,interval_seconds,enabled,updated_by)
VALUES
 ('Demo · Daily log retention','app.scheduler.tasks.automation.cleanup_task_logs','platform','Synthetic local schedule for inspecting a daily UTC trigger.','crontab','0','2','*','*','*',NULL,true,'local-demo'),
 ('Demo · Weekday log retention','app.scheduler.tasks.automation.cleanup_task_logs','etl-pipeline','Synthetic local schedule for testing weekday rules and domain filters.','crontab','30','9','1-5','*','*',NULL,false,'local-demo'),
 ('Demo · Interval log retention','app.scheduler.tasks.automation.cleanup_task_logs','ai-collect','Synthetic local schedule for testing interval editing.','interval','*','*','*','*','*',3600,true,'local-demo'),
 ('Demo · Monthly log retention','app.scheduler.tasks.automation.cleanup_task_logs','data-lake','Synthetic local schedule for inspecting a monthly trigger.','crontab','0','3','*','1','*',NULL,false,'local-demo')
ON CONFLICT(task_name) DO UPDATE SET description=excluded.description;

INSERT INTO app.automation_tasks(id,name,product_domain,task_type,task_path,status,progress,records,throughput,
  schedule,parameters,policies,owner,started_at,updated_at)
SELECT ('a0000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
  (ARRAY['Demo · Patent enrichment','Demo · Company reconciliation','Demo · Media validation','Demo · Industry aggregation','Demo · Expert graph refresh','Demo · Waiting for review'])[n],
  (ARRAY['ai-collect','etl-pipeline','data-lake','data-cockpit','knowledge-graph','platform'])[n],
  'celery','app.scheduler.tasks.automation.cleanup_task_logs',
  (ARRAY['completed','failed','running','completed','paused','queued'])[n],
  (ARRAY[100,38,64,100,42,0])[n],(ARRAY[1240,385,642,820,210,0])[n],0,
  '{"mode":"once","enabled":true}', '{"synthetic":true}', '{"synthetic":true}', 'Local demo',
  CASE WHEN n=6 THEN NULL ELSE now()-n*interval '12 minutes' END,now()-n*interval '2 minutes'
FROM generate_series(1,6) AS n
ON CONFLICT(id) DO UPDATE SET status=excluded.status,progress=excluded.progress,records=excluded.records,
  started_at=excluded.started_at,updated_at=excluded.updated_at;

-- Two historical executions per started task; timestamps and outcomes are synthetic.
INSERT INTO app.automation_task_runs(id,task_id,status,started_at,ended_at,log_count,inserted_records)
SELECT ('b0000000-0000-4000-8000-'||lpad((n*10+batch)::text,12,'0'))::uuid,
  ('a0000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
  CASE WHEN batch=1 THEN 'completed' ELSE (ARRAY['completed','failed','running','completed','paused'])[n] END,
  now()-CASE WHEN batch=1 THEN interval '1 day' ELSE n*interval '12 minutes' END,
  CASE WHEN batch=1 THEN now()-interval '23 hours 52 minutes'
    WHEN n IN (1,2,4) THEN now()-n*interval '12 minutes'+interval '5 minutes' ELSE NULL END,
  4,CASE WHEN n=1 THEN 1240 WHEN n=4 THEN 820 ELSE NULL END
FROM generate_series(1,5) AS n CROSS JOIN generate_series(1,2) AS batch
ON CONFLICT(id) DO UPDATE SET started_at=excluded.started_at,ended_at=excluded.ended_at;
UPDATE app.automation_tasks task SET current_run_id=run.id,celery_task_id=run.id::text
FROM app.automation_task_runs run
WHERE task.owner='Local demo' AND task.id=run.task_id AND right(run.id::text,1)='2';
DELETE FROM app.automation_task_logs WHERE task_id IN (
 SELECT id FROM app.automation_tasks WHERE owner='Local demo' AND parameters->>'synthetic'='true'
);
INSERT INTO app.automation_task_logs(task_id,run_id,level,message,created_at)
SELECT run.task_id,run.id,CASE WHEN run.status='failed' AND line=4 THEN 'warn' WHEN line=4 THEN 'ok' ELSE 'info' END,
  CASE line WHEN 1 THEN '[DEMO] Synthetic local preview; no worker job was executed.'
    WHEN 2 THEN '[DEMO] Loaded 1,240 synthetic records for visual inspection.'
    WHEN 3 THEN '[DEMO] Validating record identifiers and required fields.'
    ELSE CASE WHEN run.status='failed' THEN '[DEMO] Validation failed: sample record is missing company_id.'
      WHEN run.status='paused' THEN '[DEMO] Preview paused for manual review.'
      WHEN run.status='running' THEN '[DEMO] Preview progress: 64%.'
      ELSE '[DEMO] Sample batch completed. Records are synthetic.' END END,
  run.started_at+line*interval '20 seconds'
FROM app.automation_task_runs run CROSS JOIN generate_series(1,4) AS line
WHERE run.task_id IN (SELECT id FROM app.automation_tasks WHERE owner='Local demo' AND parameters->>'synthetic'='true');
COMMIT;

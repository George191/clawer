# 执行批次迁移

共享执行关系为 `automation_tasks` → `automation_task_runs` → `automation_task_logs`，与 `automation_workflows` 共用业务 PostgreSQL。采集任务通过可选的模板名和版本关联 `ai_collect_templates`；系统任务直接保存 Celery 任务路径。
每次开始、重新执行或周期调度创建批次，暂停续跑保留批次。`product_domain` 区分产品域，`task_type` 区分 `template` 和 `celery`；迁移优先保留原 `parameters.product_domain`，没有产品域的采集任务默认为 `ai-collect` / `template`。

`celery` 处理器接收 `(task_id, parameters)`，按当前 `celery_task_id` 校验派发，并将状态和日志写入共享存储；系统清理处理器是该约定的实现。模板执行仍由现有采集 Worker 处理。

五项计数只保存在执行批次中。新增、修改、删除按成功保存的记录计数，下载按成功完成下载的记录计数，同步按成功确认的记录版本计数；下载产生新版本后再次同步会再次计数。

## 上线顺序

1. 构建本版本的前端和应用镜像。
2. 停止旧版本 Web、自动化服务、Celery Beat、调度 Worker、采集 Worker、下载和同步进程，避免旧进程继续访问即将改名的表或已删除的任务字段。
3. 已有采集数据库先完成批次迁移（已完成的可跳过），再迁移共享执行表：

   ```sh
   python -m app.web.migrate_task_runs
   python -m app.web.migrate_task_runs --apply
   python -m app.web.migrate_automation_tasks
   python -m app.web.migrate_automation_tasks --apply
   ```

   如本地配置仍指向旧地址，可仅覆盖数据库主机：

   ```sh
   python -m app.web.migrate_task_runs --host 192.168.20.62
   python -m app.web.migrate_task_runs --host 192.168.20.62 --apply
   python -m app.web.migrate_automation_tasks --host 192.168.20.62
   python -m app.web.migrate_automation_tasks --host 192.168.20.62 --apply
   ```

   空库可直接执行 `migrate_automation_tasks --apply` 创建共享执行表；采集模板和分析表仍使用采集初始化流程，不属于此迁移。

4. 重建并启动本版本所有表消费者，包括 Web、自动化服务、Beat、调度/采集 Worker、下载和同步进程，检查历史批次、当前日志、下一次执行的批次统计与系统清理任务。

两个迁移默认只读检查，只有 `--apply` 写入。迁移各自在单个事务内执行，获取不到写锁会在 5 秒后退出。失败会回滚，不会留下半迁移状态。成功回执分别写入 `outputs/task_runs_migration/` 和 `outputs/automation_tasks_migration/`。

共享表迁移通过改名保留任务、批次、日志、主键及外键，同时将可选的 `ai_collect_task_stats_legacy` 改名为 `automation_task_stats_legacy`。迁移可重复执行；发现任意一对新旧表同时存在会明确失败，需先核实数据来源。完成后不再运行旧的批次迁移，也不提供旧表名兼容视图，因此所有消费者必须同步更新。`scheduler_tasks` 仍为独立的 Beat 调度配置表。

## 历史数据与限制

- 原批次迁移将已有 `run_id` 的日志按原批次回填开始时间和日志数量；尚未分组的旧日志归到单独的历史批次。
- 活动任务的本次执行尚无日志时，补建活动批次，避免续跑误用上次执行。
- 迁移不自动还原历史五项计数，保留为 `NULL`，界面按 `0` 显示；活动批次续跑后以 `0` 为基数累计新的成功操作，此前操作需核实后单独补计。任务表原计数备份到 `automation_task_stats_legacy` 后删除原列。
- 迁移可重复执行；已有批次的状态和五项计数不会被历史回填覆盖。
- MongoDB 业务写入与 PostgreSQL 统计写入没有跨库事务。业务成功而统计写入失败时，计数可能少计，当前没有自动补计。

## 日志保留

系统清理任务保存在 `automation_tasks` 中，固定 ID 为 `md5('automation:task_log_cleanup')::uuid`，名称为“系统任务日志清理”，产品域为 `platform`，类型为 `celery`，执行路径为 `app.scheduler.tasks.automation.cleanup_task_logs`。默认每天北京时间 03:00，由共享任务调度器派发到 `scheduler` 队列，每次执行使用同一套批次和日志记录。重复迁移不会覆盖该任务已有的设置。

清理结束时间早于一个自然月前的 `completed`、`failed`、`canceled`、`legacy` 批次；没有结束时间的历史批次按开始时间判断。运行中和暂停中的批次保留。删除批次时，外键级联删除其全部日志，并将指向该批次的 `current_run_id` 置空；任务和模板保留。

部署必须先执行上述共享表迁移，再重建并重启所有消费者。

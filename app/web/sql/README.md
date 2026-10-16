# 执行批次迁移

关系为模板 → 任务 → `ai_collect_task_runs` → `ai_collect_task_logs`。
所有模板共用执行批次表，通过任务关联模板；每次开始、重新执行或周期调度创建批次，暂停续跑保留批次。

五项计数只保存在执行批次中。新增、修改、删除按成功保存的记录计数，下载按成功完成下载的记录计数，同步按成功确认的记录版本计数；下载产生新版本后再次同步会再次计数。

## 上线顺序

1. 构建本版本的前端和应用镜像。
2. 停止旧版本 Web、Celery Beat、调度 Worker、采集 Worker、下载和同步进程，避免旧进程继续访问即将删除的任务字段。
3. 使用项目现有数据库配置检查迁移状态，再执行迁移：

   ```sh
   python -m app.web.migrate_task_runs
   python -m app.web.migrate_task_runs --apply
   ```

   如本地配置仍指向旧地址，可仅覆盖数据库主机：

   ```sh
   python -m app.web.migrate_task_runs --host 192.168.20.62
   python -m app.web.migrate_task_runs --host 192.168.20.62 --apply
   ```

4. 启动本版本的应用进程，检查历史批次、当前日志以及下一次执行的批次统计。

迁移在单个事务内执行，获取不到写锁会在 5 秒后退出。失败会回滚，不会留下半迁移状态。成功回执写入 `outputs/task_runs_migration/`。

## 历史数据与限制

- 已有 `run_id` 的日志按原批次回填开始时间和日志数量；尚未分组的旧日志归到单独的历史批次。
- 活动任务的本次执行尚无日志时，补建活动批次，避免续跑误用上次执行。
- 迁移不自动还原历史五项计数，保留为 `NULL`，界面按 `0` 显示；活动批次续跑后以 `0` 为基数累计新的成功操作，此前操作需核实后单独补计。任务表原计数备份到 `ai_collect_task_stats_legacy` 后删除原列。
- 迁移可重复执行；已有批次的状态和五项计数不会被历史回填覆盖。
- MongoDB 业务写入与 PostgreSQL 统计写入没有跨库事务。业务成功而统计写入失败时，计数可能少计，当前没有自动补计。

## 日志保留

Celery Beat 内置 `workspace_task_log_cleanup` 系统任务，每天北京时间 03:00（UTC 19:00）在 `scheduler` 队列执行，数据库调度配置重载不会移除此任务。

清理结束时间早于一个自然月前的 `completed`、`failed`、`canceled`、`legacy` 批次；没有结束时间的历史批次按开始时间判断。运行中和暂停中的批次保留。删除批次时，外键级联删除其全部日志，并将指向该批次的 `current_run_id` 置空；任务和模板保留。

部署后重启 Celery Beat 和 `scheduler-worker` 生效，无需新增数据库迁移。

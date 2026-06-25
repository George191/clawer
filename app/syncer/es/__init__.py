"""ES 同步器 — RDS 数据库到 Elasticsearch 的实时同步。

架构与 app/syncer/ 保持一致:
- worker.py: EsSyncWorker 主类（对应 SyncWorker）
- main.py: CLI 入口（对应 syncer/main.py）
- config.py: 同步配置与字段映射
- watermark.py: 增量水位线存储
- metrics.py: 监控指标

数据流:
    ts_rds.rds_<table> → EsSyncWorker → Elasticsearch index

增量同步:
    通过 updated_at 水位线跟踪同步进度，每次只同步新增/修改的记录。
"""

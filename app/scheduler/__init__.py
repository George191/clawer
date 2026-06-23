"""调度器模块 — 任务调度、持久化、监控。

组件:
    - celery_app: Celery 应用实例 + Beat 定时调度
    - task_store: MongoDB 任务执行记录持久化
    - beat_schedule: Beat 定时任务配置
    - monitor: 任务健康度监控与告警
    - priority_queue: 采集任务优先级队列（内存）
    - request_fallback: 请求失败自动降级策略
"""
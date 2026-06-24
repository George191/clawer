"""Google Patent 采集任务包。

模块组成:
    crawler.py   — 纯采集逻辑（无 Celery/CLI 依赖，可独立测试）
    tasks.py     — Celery task 定义
    __main__.py  — CLI 入口（手动执行，不依赖 Celery）
"""

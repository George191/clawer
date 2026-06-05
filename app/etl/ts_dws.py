"""ETL DWS 层 — 数据汇总层（Data Warehouse Summary）。

职责：
- 从 Kafka 消费 DWD 明细数据 (spider-dwd-processed)
- 按多维度聚合统计指标（专利权人、IPC分类、申请年份等）
- 入库 ts_dws 汇总表
- 推送到 ADS/应用层 Kafka Topic

继承自 ETLBase，通过反射机制自动发现 _handler_<表名> 方法。
"""

from __future__ import annotations

import logging

from app.config.settings import settings
from app.etl.base import ETLBase

logger = logging.getLogger(__name__)


class TsDws(ETLBase):
    _layer = "dws"
    _consumer_topics = [settings.etl_dwd_topic]
    _consumer_group = settings.etl_dws_consumer_group
    _producer_topic = settings.etl_dws_topic
    _producer_client_id = "etl-ts-dws-producer"

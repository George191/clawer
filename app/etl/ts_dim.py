"""ETL DIM 层 — 维度数据层（Dimension）。

职责：
- 维护数据字典、分类体系、映射关系等维度数据
- 支持 IPC/CPC 分类码表、专利权人归一化映射、法律状态码表等
- 入库 ts_dim 维度表
- 维度数据可供各层 JOIN 查询使用

继承自 ETLBase，通过反射机制自动发现 _handler_<表名> 方法。
"""

from __future__ import annotations

import logging

from app.config.settings import settings
from app.etl.base import ETLBase

logger = logging.getLogger(__name__)


class TsDim(ETLBase):
    _layer = "dim"
    _consumer_topics = [settings.etl_ods_topic]
    _consumer_group = settings.etl_dim_consumer_group
    _producer_topic = settings.etl_dim_topic
    _producer_client_id = "etl-ts-dim-producer"

"""Kafka 配置工具 — 封装统一的 SASL 认证参数构建。

所有 Kafka 连接点（Producer/Consumer/AdminClient）应调用此模块的函数，
避免重复拼接认证参数。

使用方式:
    from app.base.kafka_config import build_kafka_kwargs, get_brokers

    kwargs = build_kafka_kwargs()
    producer = AIOKafkaProducer(**kwargs)
    consumer = AIOKafkaConsumer(topic, **kwargs)
    admin_client = AIOKafkaAdminClient(**kwargs)
"""

from __future__ import annotations

from typing import Any

from app.config.settings import settings


def get_brokers(bootstrap_servers: str | None = None) -> list[str]:
    """获取 Kafka Broker 列表。

    Args:
        bootstrap_servers: 可选的覆盖值，默认为 settings.kafka_brokers

    Returns:
        Broker 地址列表
    """
    brokers_str = bootstrap_servers or settings.kafka_brokers
    return [b.strip() for b in brokers_str.split(",") if b.strip()]


def build_kafka_kwargs(**extra: Any) -> dict[str, Any]:
    """构建 Kafka 连接参数字典（包含 SASL 认证）。

    Args:
        **extra: 额外的连接参数，将覆盖默认值

    Returns:
        可直接传递给 AIOKafkaProducer/Consumer/AdminClient 的参数字典
    """
    kwargs: dict[str, Any] = {
        "bootstrap_servers": get_brokers(),
        "client_id": settings.kafka_client_id or "spider",
    }

    # SASL 认证配置
    if settings.kafka_sasl_mechanism:
        kwargs.update(
            {
                "security_protocol": settings.kafka_security_protocol,
                "sasl_mechanism": settings.kafka_sasl_mechanism,
                "sasl_plain_username": settings.kafka_sasl_username,
                "sasl_plain_password": settings.kafka_sasl_password,
            }
        )

    kwargs.update(extra)
    return kwargs


def build_producer_kwargs(**extra: Any) -> dict[str, Any]:
    """构建 Kafka Producer 连接参数。"""
    import json

    kwargs = build_kafka_kwargs(**extra)
    kwargs.setdefault(
        "value_serializer",
        lambda v: json.dumps(v, ensure_ascii=False, default=str).encode("utf-8"),
    )
    kwargs.setdefault("key_serializer", lambda k: k.encode("utf-8") if k else None)
    kwargs.setdefault("acks", "all")
    kwargs.setdefault("enable_idempotence", settings.kafka_enable_idempotence)
    kwargs.setdefault("max_request_size", 1048576)
    kwargs.setdefault("request_timeout_ms", 30000)
    kwargs.setdefault("connections_max_idle_ms", 540000)
    return kwargs


def build_consumer_kwargs(**extra: Any) -> dict[str, Any]:
    """构建 Kafka Consumer 连接参数。"""
    import json

    kwargs = build_kafka_kwargs(**extra)
    kwargs.setdefault(
        "value_deserializer",
        lambda v: json.loads(v.decode("utf-8")) if v else None,
    )
    kwargs.setdefault("key_deserializer", lambda k: k.decode("utf-8") if k else None)
    kwargs.setdefault("auto_offset_reset", "earliest")
    kwargs.setdefault("enable_auto_commit", False)
    kwargs.setdefault("max_poll_records", 100)
    kwargs.setdefault("session_timeout_ms", 30000)
    kwargs.setdefault("heartbeat_interval_ms", 10000)
    return kwargs


def build_admin_client_kwargs(**extra: Any) -> dict[str, Any]:
    """构建 Kafka AdminClient 连接参数。"""
    return build_kafka_kwargs(**extra)

"""Kafka connection configuration regression tests; no broker required."""

import ssl
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.base import kafka


@pytest.fixture(autouse=True)
def kafka_settings(monkeypatch):
    config = SimpleNamespace(
        kafka_brokers=" broker-a:9092, ,broker-b:9092 ",
        kafka_client_id="",
        kafka_sasl_mechanism="",
        kafka_security_protocol="PLAINTEXT",
        kafka_sasl_username="user",
        kafka_sasl_password="password",
        kafka_enable_idempotence=True,
    )
    monkeypatch.setattr(kafka, "settings", config)
    return config


def test_brokers_parse_settings_and_explicit_override():
    assert kafka.get_brokers() == ["broker-a:9092", "broker-b:9092"]
    assert kafka.get_brokers("other:9093, ") == ["other:9093"]


def test_plaintext_defaults_and_connection_overrides():
    assert kafka.build_kafka_kwargs() == {
        "bootstrap_servers": ["broker-a:9092", "broker-b:9092"],
        "client_id": "spider",
    }
    assert kafka.build_admin_client_kwargs(
        bootstrap_servers=["override:9093"], client_id="admin", request_timeout_ms=1000,
    ) == {
        "bootstrap_servers": ["override:9093"],
        "client_id": "admin",
        "request_timeout_ms": 1000,
    }


def test_sasl_credentials_and_override(kafka_settings):
    kafka_settings.kafka_client_id = "configured-client"
    kafka_settings.kafka_sasl_mechanism = "PLAIN"
    kafka_settings.kafka_security_protocol = "SASL_PLAINTEXT"

    kwargs = kafka.build_kafka_kwargs(sasl_plain_password="override-password")

    assert kwargs["client_id"] == "configured-client"
    assert kwargs["security_protocol"] == "SASL_PLAINTEXT"
    assert kwargs["sasl_mechanism"] == "PLAIN"
    assert kwargs["sasl_plain_username"] == "user"
    assert kwargs["sasl_plain_password"] == "override-password"


@pytest.mark.parametrize("protocol", ["SSL", "SASL_SSL"])
def test_ssl_context_preserves_existing_certificate_policy(
    monkeypatch, kafka_settings, protocol,
):
    kafka_settings.kafka_security_protocol = protocol
    context = SimpleNamespace(check_hostname=True, verify_mode=ssl.CERT_REQUIRED)
    create_context = Mock(return_value=context)
    monkeypatch.setattr(kafka.ssl, "create_default_context", create_context)

    kwargs = kafka.build_kafka_kwargs()

    create_context.assert_called_once_with()
    assert kwargs["ssl_context"] is context
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE


def test_producer_consumer_serialization_and_transport_defaults():
    producer = kafka.build_producer_kwargs()
    consumer = kafka.build_consumer_kwargs()
    timestamp = datetime(2026, 10, 16, tzinfo=timezone.utc)
    value = {"title": "记录", "created_at": timestamp}

    encoded = producer["value_serializer"](value)
    assert "记录".encode() in encoded
    assert consumer["value_deserializer"](encoded) == {
        "title": "记录", "created_at": str(timestamp),
    }
    assert consumer["key_deserializer"](producer["key_serializer"]("记录")) == "记录"
    assert producer["key_serializer"](None) is None
    assert consumer["key_deserializer"](None) is None
    assert consumer["value_deserializer"](None) is None
    assert producer["acks"] == "all"
    assert producer["enable_idempotence"] is True
    assert producer["max_request_size"] == 4 * 1024 * 1024
    assert producer["request_timeout_ms"] == 30000
    assert producer["connections_max_idle_ms"] == 540000
    assert consumer["auto_offset_reset"] == "earliest"
    assert consumer["enable_auto_commit"] is False
    assert consumer["max_poll_records"] == 100
    assert consumer["max_partition_fetch_bytes"] == producer["max_request_size"]
    assert consumer["session_timeout_ms"] == 30000
    assert consumer["heartbeat_interval_ms"] == 10000


def test_producer_and_consumer_preserve_caller_options(kafka_settings):
    kafka_settings.kafka_enable_idempotence = False
    assert kafka.build_producer_kwargs()["enable_idempotence"] is False
    serializer = Mock()
    producer = kafka.build_producer_kwargs(
        value_serializer=serializer, acks=1, enable_idempotence=False,
    )
    consumer = kafka.build_consumer_kwargs(
        value_deserializer=None, enable_auto_commit=True, max_poll_records=5,
    )

    assert producer["value_serializer"] is serializer
    assert producer["acks"] == 1
    assert producer["enable_idempotence"] is False
    assert consumer["value_deserializer"] is None
    assert consumer["enable_auto_commit"] is True
    assert consumer["max_poll_records"] == 5


def test_kafka_module_has_no_record_business_exports():
    assert not hasattr(kafka, "KafkaProducer")
    assert not hasattr(kafka, "RecordPublisher")
    assert not hasattr(kafka, "SYNC_DATA_TYPES")

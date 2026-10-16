"""兼容已发布适配器的 Kafka 记录发布入口。"""

from app.syncer.publisher import RecordPublisher as KafkaProducer

__all__ = ["KafkaProducer"]

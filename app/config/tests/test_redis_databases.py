from app.config.settings import redis_url_with_database
from app.etl.offset_manager import OffsetManager


def test_redis_url_with_database_replaces_path_and_query_database() -> None:
    assert (
        redis_url_with_database(
            "rediss://user:p%40ss@example.test:6380/9?db=8&socket_timeout=1",
            2,
        )
        == "rediss://user:p%40ss@example.test:6380/2?db=2&socket_timeout=1"
    )


def test_redis_url_with_database_supports_unix_socket_urls() -> None:
    assert (
        redis_url_with_database("redis+unix:///tmp/redis.sock?db=9", 1)
        == "redis+unix:/tmp/redis.sock?db=1"
    )


def test_etl_offset_key_uses_database_as_business_namespace() -> None:
    manager = OffsetManager()

    assert manager._make_key("rds", "spider-crawler", 0) == (
        "offset:rds:spider-crawler:0"
    )

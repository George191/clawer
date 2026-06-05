"""全局配置 — 通过环境变量 `.env` 注入的 Pydantic Settings。

配置项按功能分组：
- 基础：日志级别、模板目录、并发数
- HTTP：请求超时、重试策略、User-Agent、代理
- 下载：分块大小、最大文件大小
- MongoDB / Redis / MinIO / Kafka：外部服务连接配置
- 反爬智能层：代理池、请求延迟、UA/Cookie 轮换
- 调度增强：域名速率控制、请求降级回退
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SPIDER_",
        case_sensitive=False,
        extra="ignore",
    )

    log_level: str = Field(default="INFO", description="日志级别")
    template_dir: str = Field(default="templates", description="网站模板配置目录")
    output_dir: str = Field(default="output", description="采集结果输出目录")
    max_concurrent_tasks: int = Field(default=5, description="最大并发任务数")

    http_request_timeout: float = Field(default=30.0, description="HTTP请求超时(秒)")
    http_download_timeout: float = Field(default=300.0, description="文件下载超时(秒), 大文件需要更长")
    http_max_retries: int = Field(default=3, description="HTTP请求最大重试次数")
    http_retry_backoff: float = Field(default=2.0, description="重试退避因子(秒)")
    http_retry_on_statuses: list[int] = Field(
        default=[429, 500, 502, 503, 504],
        description="触发重试的HTTP状态码",
    )
    http_user_agent: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        description="请求User-Agent",
    )
    http_proxy: str = Field(default="", description="HTTP代理地址")
    http_verify_ssl: bool = Field(default=False, description="是否验证SSL证书")

    download_chunk_size: int = Field(
        default=8192, description="文件下载流式写入块大小(bytes)"
    )
    download_max_file_size: int = Field(
        default=500 * 1024 * 1024, description="允许下载的最大文件大小(bytes)"
    )

    db_url: str = Field(default="", description="MongoDB连接URL, 留空则仅使用文件存储")
    db_name: str = Field(default="", description="MongoDB数据库名")
    redis_url: str = Field(default="", description="Redis连接URL, 留空则不使用去重缓存")

    minio_endpoint: str = Field(default="", description="MinIO服务地址, 如 localhost:9000")
    minio_access_key: str = Field(default="", description="MinIO Access Key")
    minio_secret_key: str = Field(default="", description="MinIO Secret Key")
    minio_bucket: str = Field(default="", description="MinIO存储桶名称")
    minio_secure: bool = Field(default=False, description="MinIO是否使用HTTPS")

    kafka_brokers: str = Field(default="", description="Kafka Broker地址, 多个以逗号分隔")
    kafka_topic: str = Field(default="", description="Kafka推送主题")
    kafka_client_id: str = Field(default="", description="Kafka客户端ID")
    kafka_enable_idempotence: bool = Field(default=True, description="是否启用幂等生产者")

    # ── Anti-Crawl Intelligence Layer (反爬智能层) ────────────────────────────
    anti_crawl_enabled: bool = Field(default=False, description="是否启用反爬智能层")

    # Tunnel Proxy (隧道代理，优先于代理池)
    tunnel_proxy_url: str = Field(
        default="", 
        description="隧道代理URL, 格式: http://user:pass@host:port, 配置后优先使用"
    )

    # Proxy Pool
    proxy_pool_file: str = Field(default="", description="代理列表文件路径, 每行一个代理URL")
    proxy_pool_api_url: str = Field(default="", description="代理池API地址, 返回JSON代理列表")
    proxy_rotation: str = Field(default="round_robin", description="代理轮换策略: round_robin | random")
    proxy_health_check_url: str = Field(default="https://httpbin.org/ip", description="代理健康检查URL")
    proxy_max_failures: int = Field(default=3, description="代理连续失败多少次后摘除")

    # Request Delay
    request_delay_min: float = Field(default=1.0, description="请求最小延迟(秒)")
    request_delay_max: float = Field(default=3.0, description="请求最大延迟(秒)")
    domain_rate_limit: dict[str, float] = Field(
        default_factory=dict,
        description="域名速率限制, 如 {\"patents.google.com\": 5.0}",
    )

    # Identity Rotation
    user_agent_pool_file: str = Field(default="", description="UA池文件路径, 每行一个UA")
    referer_enabled: bool = Field(default=False, description="是否启用Referer伪造")
    cookie_pool_file: str = Field(default="", description="Cookie池文件路径")
    identity_rotation_interval: int = Field(default=0, description="多少次请求后轮换身份, 0表示每次请求")

    # ── Redis Dedup & Bloom Filter (Redis 去重 & 布隆过滤器) ────────────────────
    dedup_enabled: bool = Field(default=False, description="是否启用Redis去重")
    dedup_key_prefix: str = Field(default="spider:dedup", description="去重Redis key前缀")
    bloom_filter_enabled: bool = Field(default=False, description="是否启用布隆过滤器")
    bloom_filter_capacity: int = Field(default=1000000, description="布隆过滤器预期元素数")
    bloom_filter_error_rate: float = Field(default=0.001, description="布隆过滤器误判率")
    bloom_filter_key_prefix: str = Field(default="spider:bloom", description="布隆过滤器key前缀")
    incremental_mode: bool = Field(default=False, description="增量采集模式: 只采新增数据")

    # ── Scheduler Enhancement (调度器增强) ────────────────────────────────────
    scheduler_enabled: bool = Field(default=False, description="是否启用增强调度器")
    rate_limit_enabled: bool = Field(default=False, description="是否启用域名级速率控制")
    domain_rate_limit_config: dict[str, float] = Field(
        default_factory=dict,
        description="域名速率配置, 如 {\"patents.google.com\": 5.0}",
    )
    fallback_enabled: bool = Field(default=False, description="是否启用请求失败自动降级")
    fallback_max_proxy_failures: int = Field(default=5, description="代理连续失败多少次后降级到直连")
    fallback_cooldown_seconds: int = Field(default=300, description="直连稳定多久后尝试恢复代理")

    # ── Template Engine Enhancement (模板引擎增强) ────────────────────────────
    jinja2_enabled: bool = Field(default=False, description="是否启用Jinja2模板引擎")
    conditional_fields_enabled: bool = Field(default=False, description="是否启用条件字段")
    pre_hooks_enabled: bool = Field(default=False, description="是否启用预处理钩子")

    # ── Postgres ETL Pipeline (Postgres ETL 管道) ─────────────────────────────
    pg_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:32775/spider_etl",
        description="Postgres 连接 URL (asyncpg 驱动)",
    )
    pg_pool_min: int = Field(default=2, description="Postgres 连接池最小值")
    pg_pool_max: int = Field(default=10, description="Postgres 连接池最大值")

    # ETL Kafka Topics
    etl_raw_topic: str = Field(
        description="ETL 原始数据输入 Topic (采集推送)",
    )
    etl_rds_topic: str = Field(
        description="ETL RDS 处理后输出 Topic",
    )
    etl_ods_topic: str = Field(
        description="ETL ODS 标准化后输出 Topic",
    )
    etl_ads_topic: str = Field(
        description="ETL ADS 算法分析后输出 Topic（应用层）",
    )
    etl_task_topic: str = Field(
        description="ETL TASK 输入 Topic (ODS 标准化后送入算法层做 PDF→Markdown)",
    )
    etl_dwd_topic: str = Field(
        description="ETL DWD 明细层输出 Topic",
    )
    etl_graph_topic: str = Field(
        description="ETL 图谱输出 Topic",
    )
    etl_dws_topic: str = Field(
        description="ETL DWS 汇总层输出 Topic",
    )
    etl_dim_topic: str = Field(
        description="ETL DIM 维度层输出 Topic",
    )

    etl_rds_consumer_group: str = Field(
        default="spider-rds-group",
        description="RDS 层 Kafka Consumer Group",
    )
    etl_ods_consumer_group: str = Field(
        default="spider-ods-group",
        description="ODS 层 Kafka Consumer Group",
    )
    etl_ads_consumer_group: str = Field(
        default="spider-ads-group",
        description="ADS 层 Kafka Consumer Group",
    )

    etl_task_consumer_group: str = Field(
        default="spider-task-group",
        description="TASK 层 Kafka Consumer Group",
    )

    etl_dwd_consumer_group: str = Field(
        default="spider-dwd-group",
        description="DWD 层 Kafka Consumer Group",
    )

    etl_graph_consumer_group: str = Field(
        default="spider-graph-group",
        description="图谱层 Kafka Consumer Group",
    )

    etl_dws_consumer_group: str = Field(
        default="spider-dws-group",
        description="DWS 层 Kafka Consumer Group",
    )
 
    etl_dim_consumer_group: str = Field(
        default="spider-dim-group",
        description="DIM 层 Kafka Consumer Group",
    )


settings = AppSettings()

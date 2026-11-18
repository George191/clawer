import os
import secrets
import warnings
from uuid import UUID
from typing import Annotated, Any, Literal
from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    computed_field,
    model_validator,
)
from pydantic_settings import SettingsConfigDict, BaseSettings
from sqlalchemy.engine import make_url
from typing_extensions import Self

from app.storage.postgres_client import _resolved_pg_url
from app.web.core.config.indexed_sample import (
    IndexedCompoundSampleScope, IndexedProjectionScope, IndexedSampleScope, RequiredIndexedPreviewTable,
)
from app.web.core.config.sandbox_handoff import HandoffClearance, HandoffCopyGrant, HandoffSinkGrant
from app.web.core.config.snapshot_intent import SnapshotDeliveryEvidenceGrant, SnapshotDeliveryGrant, SnapshotIntentGrant

def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ONTOLOGY_WRITEBACK_RECEIVERS_FILE: str | None = None

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15

    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    TRANSFER_SANDBOX_ENABLED: bool = False
    TRANSFER_SANDBOX_SCHEDULER_ENABLED: bool = False
    TRANSFER_SANDBOX_WEEKLY_SCHEDULER_ENABLED: bool = False
    TRANSFER_SANDBOX_MONTHLY_SCHEDULER_ENABLED: bool = False
    TRANSFER_SANDBOX_UPSTREAM_ENABLED: bool = False
    TRANSFER_SANDBOX_FRESHNESS_ENABLED: bool = False
    TRANSFER_SANDBOX_COLLECTED_STATS_ENABLED: bool = False
    TRANSFER_SANDBOX_JOB_QUALITY_ENABLED: bool = False
    TRANSFER_SANDBOX_WEEKLY_JOB_QUALITY_ENABLED: bool = False
    TRANSFER_SANDBOX_FIELD_FRESHNESS_ENABLED: bool = False
    TRANSFER_SANDBOX_MIN_ROWS_ENABLED: bool = False
    TRANSFER_SANDBOX_OBSERVED_LINEAGE_ENABLED: bool = False
    TRANSFER_SANDBOX_SCHEDULED_OBSERVED_LINEAGE_ENABLED: bool = False
    TRANSFER_SANDBOX_MIXED_UPSTREAM_ENABLED: bool = False
    TRANSFER_SANDBOX_JOB_CANCEL_ENABLED: bool = False
    CDC_SANDBOX_ENABLED: bool = False
    CDC_SANDBOX_VERSIONS_ENABLED: bool = False
    ONTOLOGY_SANDBOX_PIPELINE_ENABLED: bool = False
    ONTOLOGY_SANDBOX_PIPELINE_RESTORE_ENABLED: bool = False
    ONTOLOGY_SANDBOX_PIPELINE_MATCH_ENABLED: bool = False
    ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED: bool = False
    ONTOLOGY_INDEX_PREVIEW_SANDBOX_ENABLED: bool = False
    ONTOLOGY_EXPLORER_INDEXED_COMPOUND_SAMPLE_ENABLED: bool = False
    ONTOLOGY_EXPLORER_INDEXED_COMPOUND_SAMPLE_PROFILE: IndexedCompoundSampleScope | None = None
    ONTOLOGY_EXPLORER_REQUIRED_INDEXED_PREVIEW_TABLES: Annotated[
        tuple[RequiredIndexedPreviewTable, ...], Field(max_length=16),
    ] = ()
    ONTOLOGY_EXPLORER_INDEXED_PROJECTION_ENABLED: bool = False
    ONTOLOGY_EXPLORER_INDEXED_PROJECTION_PROFILE: IndexedProjectionScope | None = None
    ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ENABLED: bool = False
    ONTOLOGY_EXPLORER_INDEXED_SAMPLE_DATABASE_MARKER: UUID | None = None
    ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ALLOWLIST: Annotated[list[IndexedSampleScope], Field(max_length=3)] = []
    PUSH_INBOX_SANDBOX_ENABLED: bool = False
    PUSH_INBOX_SANDBOX_INTAKE_CONTROL_ENABLED: bool = False
    PUSH_INBOX_SANDBOX_APPLY_ENABLED: bool = False
    PUSH_INBOX_SANDBOX_EVENT_PATH_ENABLED: bool = False
    PUSH_INBOX_SANDBOX_PUBLIC_KEY: SecretStr | None = None
    PUSH_INBOX_SANDBOX_SECONDARY_ENABLED: bool = False
    PUSH_INBOX_SANDBOX_SECONDARY_PUBLIC_KEY: SecretStr | None = None
    PUSH_INBOX_SANDBOX_DATABASE_MARKER: UUID | None = None
    ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED: bool = False
    ONTOLOGY_SANDBOX_HANDOFF_ENABLED: bool = False
    ONTOLOGY_SANDBOX_HANDOFF_SINK_GENERATION: UUID | None = None
    ONTOLOGY_SANDBOX_HANDOFF_SINK_CLEARANCE: HandoffClearance | None = None
    ONTOLOGY_SANDBOX_HANDOFF_SINK_GRANTS: dict[UUID, HandoffSinkGrant] = {}
    ONTOLOGY_SANDBOX_HANDOFF_COPY_ENABLED: bool = False
    ONTOLOGY_SANDBOX_HANDOFF_SENDER_PROOF_ENABLED: bool = False
    ONTOLOGY_SANDBOX_HANDOFF_COPY_DATABASE_MARKER: UUID | None = None
    ONTOLOGY_SANDBOX_HANDOFF_COPY_PHYSICAL_DIGEST: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    ONTOLOGY_SANDBOX_HANDOFF_COPY_GRANTS: dict[UUID, HandoffCopyGrant] = {}
    ONTOLOGY_SANDBOX_HANDOFF_PROOF_ENABLED: bool = False
    ONTOLOGY_SANDBOX_HANDOFF_PROOF_STREAM_MARKER: UUID | None = None
    ONTOLOGY_SANDBOX_HANDOFF_PROOF_GRANTS: dict[UUID, HandoffCopyGrant] = {}
    ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS: dict[UUID, list[Literal["sandbox-synthetic", "sandbox-restricted"]]] = {}
    CDC_SANDBOX_STREAM_MARKER: UUID | None = None
    BATCH_SYNC_SANDBOX_ENABLED: bool = False
    BATCH_SYNC_SOURCE_IDENTITY_ENABLED: bool = False
    BATCH_SYNC_INCREMENTAL_PREFLIGHT_ENABLED: bool = False
    BATCH_SYNC_INCREMENTAL_ENABLED: bool = False
    BATCH_SYNC_RANGE_SNAPSHOT_ENABLED: bool = False
    BATCH_SYNC_SOURCE_IDENTITY_MARKER: UUID | None = None
    BATCH_SYNC_SANDBOX_SCHEDULER_ENABLED: bool = False
    ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED: bool = False
    TABLE_EXPORT_SANDBOX_ENABLED: bool = False
    TABLE_EXPORT_SOURCE_MARKER: UUID | None = None
    TABLE_EXPORT_RECEIVER_HOST: str = ""
    TABLE_EXPORT_RECEIVER_PORT: int = 5432
    TABLE_EXPORT_RECEIVER_DATABASE: str = ""
    TABLE_EXPORT_RECEIVER_USERNAME: str = ""
    TABLE_EXPORT_RECEIVER_PASSWORD: SecretStr | None = None
    TABLE_EXPORT_RECEIVER_MARKER: UUID | None = None
    TABLE_EXPORT_RECEIVER_TLS: bool = False
    TABLE_EXPORT_SECONDARY_ENABLED: bool = False
    TABLE_EXPORT_APPEND_ENABLED: bool = False
    TABLE_EXPORT_SECONDARY_RECEIVER_HOST: str = ""
    TABLE_EXPORT_SECONDARY_RECEIVER_PORT: int = 5432
    TABLE_EXPORT_SECONDARY_RECEIVER_DATABASE: str = ""
    TABLE_EXPORT_SECONDARY_RECEIVER_USERNAME: str = ""
    TABLE_EXPORT_SECONDARY_RECEIVER_PASSWORD: SecretStr | None = None
    TABLE_EXPORT_SECONDARY_RECEIVER_MARKER: UUID | None = None
    TABLE_EXPORT_SECONDARY_RECEIVER_TLS: bool = False
    TABLE_EXPORT_SECONDARY_CONFIG_REVISION: UUID | None = None
    TABLE_EXPORT_SNAPSHOT_PREVIEW_ENABLED: bool = False
    TABLE_EXPORT_SNAPSHOT_PREVIEW_CONFIG_REVISION: UUID | None = None
    TABLE_EXPORT_SNAPSHOT_PREVIEW_HOST: str = ""
    TABLE_EXPORT_SNAPSHOT_PREVIEW_PORT: int = 0
    TABLE_EXPORT_SNAPSHOT_PREVIEW_DATABASE: str = ""
    TABLE_EXPORT_SNAPSHOT_PREVIEW_USERNAME: str = ""
    TABLE_EXPORT_SNAPSHOT_PREVIEW_PASSWORD: SecretStr | None = None
    TABLE_EXPORT_SNAPSHOT_PREVIEW_MARKER: UUID | None = None
    TABLE_EXPORT_SNAPSHOT_PREVIEW_PROOF_SHA256: str = ""
    TABLE_EXPORT_SNAPSHOT_INTENT_ENABLED: bool = False
    TABLE_EXPORT_SNAPSHOT_DELIVERY_WEB_ENABLED: bool = False
    TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED: bool = False
    TABLE_EXPORT_SNAPSHOT_INTENT_GRANTS: dict[UUID, SnapshotIntentGrant] = {}
    TABLE_EXPORT_SNAPSHOT_DELIVERY_ENABLED: bool = False
    TABLE_EXPORT_SNAPSHOT_DELIVERY_GRANTS: dict[UUID, SnapshotDeliveryGrant] = {}
    TABLE_EXPORT_SNAPSHOT_DELIVERY_EVIDENCE_ENABLED: bool = False
    TABLE_EXPORT_SNAPSHOT_DELIVERY_EVIDENCE_GRANTS: dict[UUID, SnapshotDeliveryEvidenceGrant] = {}
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_ENABLED: bool = False
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_CONFIG_REVISION: UUID | None = None
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_HOST: str = ""
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_PORT: int = 0
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_DATABASE: str = ""
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_USERNAME: str = ""
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_PASSWORD: SecretStr | None = None
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_MARKER: UUID | None = None
    TABLE_EXPORT_SNAPSHOT_PUBLICATION_PROOF_SHA256: str = ""

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_MIGRATE_DATABASE_URI(self) -> PostgresDsn:
        return PostgresDsn(
            make_url(_resolved_pg_url()).set(drivername="postgresql+psycopg2").render_as_string(hide_password=False)
        )
        
    
    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_RUNNABLE_DATABASE_URI(self) -> PostgresDsn:
        return PostgresDsn(_resolved_pg_url())
    
    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: EmailStr | None = None

    PROTECTED_NAMES: list[str] = ["user", "ignore", "error"]

    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str 
    AWS_S3_ENDPOINT_URL: str
    AWS_S3_BUCKET_NAME: str
    AWS_S3_UPLOAD_BUFFER: int = 5 * 1024 * 1024  # 5MB 分块大小
    AWS_S3_DOWNLOAD_BUFFER: int = 1024 * 1024
    AWS_S3_UPLOAD_PREFIX: str

    """Milvus 配置"""
    MILVUS_HOST: str
    MILVUS_PORT: int
    MILVUS_USER: str | None = None
    MILVUS_PASSWORD: str | None = None
    MILVUS_DB_NAME: str = "default"
    MILVUS_ASYNC: bool = True
    MILVUS_INDEX_TYPE: str = "IVF_FLAT"
    MILVUS_DIMENSION: int = 1024
    MILVUS_METRIC_TYPE: str = "L2"
    MILVUS_MAX_RETRIES: int = 3
    MILVUS_RETRY_DELAY: float = 1.0
    MILVUS_INDEX_PARAMS: dict[str, Any] = {
        "nlist": 1024,
        "nprobe": 10,
        "ef": 1024,
        "m": 16,
        "ef_construction": 1024,
    }

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str
    FIRST_SUPERUSER_PHONE: str
    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("SPIDER_PG_URL", make_url(_resolved_pg_url()).password or "")
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self
    
    MODEL_PROVIDER_ENCRYPTION_KEY: str = ""

    LOGGING_DIR: str = 'logs'

    if not os.path.exists(LOGGING_DIR): 
        os.makedirs(LOGGING_DIR)

    SERVER_LOGS_FILE: str = os.path.join(LOGGING_DIR, 'server.log')
    ERROR_LOGS_FILE: str = os.path.join(LOGGING_DIR, 'error.log')

    LOGGING: dict[str, Any] = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'console': {
                'format': '%(asctime)s [%(levelname)s] - %(message)s',
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            'simple': {
                'format': '%(asctime)s [%(trace_id)s] [%(name)s] [%(filename)s:%(lineno)d] [%(levelname)s] - %(message)s',
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            'standard': {
                'format': '{"time": "%(asctime)s", "level": "%(levelname)s", "func": "%(name)s.%(module)s.%(funcName)s:%(lineno)d", "trace_id": "%(trace_id)s", "message": "%(message)s"}',
                'datefmt': '%Y-%m-%d %H:%M:%S'
            }
        },
        'filters': {
            'info_filter': "app.web.utils.logger.filters.InfoFilter",
            'trace_id_filter': "app.web.utils.logger.filters.TraceIdFilter"
        },
        'handlers': {
            'error_file': {
                'level': 'ERROR',
                'class': 'aiologger.handlers.files.AsyncTimedRotatingFileHandler',
                'filename': ERROR_LOGS_FILE,
                'when': "midnight",
                'backup_count': 7,  # 保留最近7天的日志文件
                'formatter': 'standard',
                'filters': ['trace_id_filter'],
                'encoding': 'utf-8'  # 设置文件编码为UTF-8
            },
            'info_file': {
                'level': 'INFO',
                'class': 'aiologger.handlers.files.AsyncTimedRotatingFileHandler',
                'filename': SERVER_LOGS_FILE,
                'when': "midnight",
                'backup_count': 7,  # 保留最近7天的日志文件
                'formatter': 'standard',
                'filters': ['trace_id_filter', 'info_filter'],
                'encoding': 'utf-8'  # 设置文件编码为UTF-8
            }
        }
    }

settings = Settings()  # type: ignore

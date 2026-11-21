from .apikey import (
    ApiKey, ApiKeyBase, ApiKeyCreate, ApiKeyOut, ApiKeyOutPublic, ApiKeyUpdate
)
from .checkpoint import (
    Checkpoint, CheckpointBlobs, CheckpointOut, Write
)
from .graph import (
    Graph, GraphBase, GraphCreate, GraphOut, GraphUpdate
)
from .provider import (
    Model, ModelCapability, ModelOut, ModelCategory,
    ModelCreate, ModelProvider, ModelProviderBase, ModelProviderCreate,
    ModelProviderOut, ModelProviderUpdate, ModelsBase, ModelUpdate
)
from .team import (
    Team, TeamBase, TeamCreate, TeamOut,
    TeamUpdate
)
from .thread import (
    Thread, ThreadBase, ThreadCreate, ThreadOut, ThreadRead, ThreadUpdate
)
from .upload import (
    Upload, UploadBase, UploadCreate, UploadOut, UploadUpdate
)
from .user import (
    UpdateLanguageMe, UpdatePassword, User, UserBase, UserCreate, UserOut,
    UserRegister, UserUpdate, UserUpdateMe, TeamUserJoin, InviteCodes, RoleTypes,
    UserContext, UserContextTeam, UserContextTenant
)
from .tenant import (
    Tenant, TenantBase, TenantCreate, TenantOut, TenantUpdate
)
from .argument import (
    Dictionary, DictionaryBase, DictionaryCreate, DictionaryOut, DictionaryUpdate,
    DictionaryArgument, DictionaryArgumentBase, DictionaryArgumentCreate, DictionaryArgumentOut,
    DictionaryArgumentUpdate
)
from .dataset import (
    Dataset, DatasetBase, DatasetCreate, DatasetOut, DatasetUpdate
)
from .embedding import (
    Embedding, EmbeddingBase, EmbeddingCreate, EmbeddingOut, EmbeddingUpdate
)

from .ontology_registry import OntologyDefinitionRow as OntologyDefinitionRow
from .explorer_layouts import ExplorerResultLayout as ExplorerResultLayout
from .explorer_properties import ExplorerPropertyProfile as ExplorerPropertyProfile
from .asset_collected_stats import AssetCollectedStatRow as AssetCollectedStatRow
from .asset_field_freshness import AssetFieldFreshnessRow as AssetFieldFreshnessRow
from .asset_min_rows import AssetMinRowsRow as AssetMinRowsRow
from .asset_job_quality import AssetJobQualityRow as AssetJobQualityRow, TransferTargetRejectionRow as TransferTargetRejectionRow
from .asset_freshness import AssetFreshnessPolicyRow as AssetFreshnessPolicyRow
from .push_inbox_apply import PushApplyBindingRow as PushApplyBindingRow, PushApplyResultRow as PushApplyResultRow
from .push_inbox import PushInboxReceiptRow as PushInboxReceiptRow, PushInboxStateRow as PushInboxStateRow
from .batch_source_identity import BatchSourceIdentityRow as BatchSourceIdentityRow
from .batch_syncs import BatchSyncPlanRow as BatchSyncPlanRow, BatchSyncWatermarkRow as BatchSyncWatermarkRow
from .cdc_sandbox import CdcSandboxBindingRow as CdcSandboxBindingRow
from .cdc_sandbox_versions import CdcSandboxVersionRow as CdcSandboxVersionRow
from .pipeline_sandbox import PipelineSandboxRevisionRow as PipelineSandboxRevisionRow
from .pipeline_sandbox_outputs import PipelineSandboxOutputRow as PipelineSandboxOutputRow
from .batch_sync_schedules import BatchSyncScheduleRow as BatchSyncScheduleRow
from .table_exports import TableExportPlanRow as TableExportPlanRow
from .transfer_plans import TransferPlanRow as TransferPlanRow
from .transfer_schedules import TransferScheduleRow as TransferScheduleRow
from .transfer_upstream import UpstreamTriggerRow as UpstreamTriggerRow
from .ontology_runtime import (
    OntologyActionEvent as OntologyActionEvent,
    OntologyRuntimeRecord as OntologyRuntimeRecord,
)

from pydantic import EmailStr, field_validator
from sqlmodel import Field, SQLModel


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class AccessToken(SQLModel):
    access_token: str
    token_type: str = "bearer"


# JSON payload containing access token & refresh token
class Token(AccessToken):
    refresh_token: str


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None
    exp: float | None = None
    type: str | None = None
    session_exp: float


class SessionUnlock(SQLModel):
    refresh_token: str
    password: str = Field(min_length=1, repr=False)


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=40)


class PasswordRecoveryCodeRequest(SQLModel):
    email: EmailStr


class PasswordRecoveryCodeChallenge(SQLModel):
    message: str
    challenge_token: str
    expires_in: int


class PasswordRecoveryCodeVerify(SQLModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("Verification code must contain six digits")
        return value


class PasswordRecoveryCodeVerified(SQLModel):
    reset_token: str
    expires_in: int


class NewPasswordWithCode(SQLModel):
    reset_token: str
    new_password: str = Field(min_length=8, max_length=40)

from .explorer_saved_items import ExplorerSavedItem
from .ontology_explorer import OntologyExplorerResultLayout, OntologyExplorerSavedItem

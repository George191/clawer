import uuid
from typing import Annotated

from pydantic import StringConstraints
from sqlmodel import Field
from app.web.api.utils.models import BaseModel


TeamName = Annotated[str, StringConstraints(
    max_length=64, pattern=r"^[a-zA-Z0-9_-]+( [a-zA-Z0-9_-]+)*$"
)]


class TeamBase(BaseModel):
    name: str
    description: str | None = None
    # 增加team的图标
    icon: str | None = None


class TeamCreate(TeamBase):
    name: TeamName


class TeamUpdate(TeamBase):
    name: TeamName | None = None  # type: ignore[assignment]
    remark: str | None = None


class Team(TeamBase, table=True):
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )
    name: str = Field(unique=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenant.id", nullable=True)


# Properties to return via API, id is always required
class TeamOut(TeamBase):
    id: uuid.UUID
    tenant_id: uuid.UUID

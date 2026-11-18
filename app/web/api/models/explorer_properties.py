from uuid import UUID, uuid4
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ExplorerPropertyProfile(SQLModel, table=True):
    __tablename__ = "explorer_property_profiles"
    schema_name: str = Field(primary_key=True, max_length=63)
    table_name: str = Field(primary_key=True, max_length=63)
    source_key: str = Field(primary_key=True, max_length=36)
    version: UUID = Field(default_factory=uuid4, nullable=False)
    settings: dict = Field(sa_column=Column(JSONB, nullable=False))

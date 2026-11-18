from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ExplorerSavedItem(SQLModel, table=True):
    __tablename__ = "explorer_saved_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID = Field(foreign_key="user.id", ondelete="CASCADE")
    schema_name: str = Field(max_length=63)
    table_name: str = Field(max_length=63)
    kind: str = Field(max_length=12)
    name: str = Field(max_length=100)
    shared: bool = False
    version: UUID = Field(default_factory=uuid4)
    content: dict = Field(sa_column=Column(JSONB(), nullable=False))

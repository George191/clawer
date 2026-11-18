from uuid import UUID, uuid4
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DBField, SQLModel


class ExplorerResultLayout(SQLModel, table=True):
    __tablename__ = "explorer_result_layouts"
    __table_args__ = (
        UniqueConstraint(
            "schema_name",
            "table_name",
            "owner_id",
            name="uq_explorer_layout_asset_owner",
            postgresql_nulls_not_distinct=True,
        ),
    )
    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    schema_name: str = DBField(max_length=63)
    table_name: str = DBField(max_length=63)
    owner_id: UUID | None = DBField(default=None, foreign_key="user.id", ondelete="CASCADE")
    layout: dict | None = DBField(sa_column=Column(JSONB(none_as_null=True), nullable=True))
    version: UUID = DBField(default_factory=uuid4)

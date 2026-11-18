"""Persistent identity and acknowledgement for an explicit external delivery."""

from uuid import UUID

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel


class OntologyDelivery(SQLModel, table=True):
    __tablename__ = "ontology_deliveries"
    __table_args__ = (
        UniqueConstraint("transaction_id", "receiver", name="uq_ontology_delivery_destination"),
        CheckConstraint(
            "state IN ('pending','failed','delivered') AND attempts >= 0",
            name="ck_ontology_delivery_state",
        ),
    )
    id: UUID = Field(primary_key=True)
    transaction_id: UUID = Field(foreign_key="ontology_transactions.id")
    receiver: str = Field(max_length=64)
    endpoint_hash: str = Field(max_length=64)
    payload_hash: str = Field(max_length=64)
    state: str = Field(default="pending", max_length=10)
    attempts: int = 0
    receipt_id: UUID | None = None
    failure: str | None = Field(default=None, max_length=100)

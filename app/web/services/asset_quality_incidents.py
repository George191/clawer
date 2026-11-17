"""In-app incident lifecycle for already confirmed sandbox check states."""

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.asset_freshness import (
    AssetFreshnessPolicyRow, AssetQualityIncident, AssetQualityIncidentClose, AssetQualityIncidentKind,
    AssetQualityIncidentLists, AssetQualityIncidentReason, AssetQualityIncidentRow,
)

_HISTORY_LIMIT = 25


def _incident(row: AssetQualityIncidentRow) -> AssetQualityIncident:
    if row.closed_at is not None:
        status = "recovered" if row.close_reason == "healthy" else "superseded"
    else:
        status = "acknowledged" if row.acknowledged_at is not None else "open"
    return AssetQualityIncident(id=row.id, generation=row.generation, kind=row.kind, status=status,
                                reason=row.reason, openedAt=row.opened_at, lastConfirmedAt=row.last_confirmed_at,
                                acknowledgedAt=row.acknowledged_at, closedAt=row.closed_at, closeReason=row.close_reason)


def _kind_filter(daily_only: bool, field_only: bool, min_rows_only: bool, weekly_only: bool):
    if weekly_only:
        return AssetQualityIncidentRow.kind == "weekly_job"
    if daily_only:
        return AssetQualityIncidentRow.kind == "daily_job"
    if field_only:
        return AssetQualityIncidentRow.kind == "field_time"
    if min_rows_only:
        return AssetQualityIncidentRow.kind == "minimum_rows"
    return AssetQualityIncidentRow.kind.notin_(("daily_job", "weekly_job", "field_time", "minimum_rows"))


async def list_quality_incidents(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, *, daily_only: bool = False,
    field_only: bool = False, min_rows_only: bool = False, weekly_only: bool = False,
) -> AssetQualityIncidentLists:
    """Return at most three active check incidents and bounded terminal history."""
    query = select(AssetQualityIncidentRow).where(
        AssetQualityIncidentRow.target_table == policy.target_table,
        _kind_filter(daily_only, field_only, min_rows_only, weekly_only),
    )
    active = (await session.scalars(query.where(
        AssetQualityIncidentRow.closed_at.is_(None), AssetQualityIncidentRow.generation == policy.generation,
    ).order_by(AssetQualityIncidentRow.kind))).all()
    history = (await session.scalars(query.where(AssetQualityIncidentRow.closed_at.is_not(None))
               .order_by(AssetQualityIncidentRow.closed_at.desc(), AssetQualityIncidentRow.id.desc())
               .limit(_HISTORY_LIMIT))).all()
    return AssetQualityIncidentLists(active=[_incident(row) for row in active], history=[_incident(row) for row in history])


async def observe_quality_incident(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, kind: AssetQualityIncidentKind, now: datetime, *,
    healthy: bool = False, failure: AssetQualityIncidentReason | None = None,
) -> None:
    """Project a confirmed sample in the same transaction that holds the policy lock."""
    if not healthy and failure is None:
        # Unavailable, unknown, stale or failed checks are not proof of recovery.
        return
    row = (await session.scalars(select(AssetQualityIncidentRow).where(
        AssetQualityIncidentRow.target_table == policy.target_table,
        AssetQualityIncidentRow.generation == policy.generation, AssetQualityIncidentRow.kind == kind,
        AssetQualityIncidentRow.closed_at.is_(None),
    ))).one_or_none()
    if healthy:
        if row is not None:
            row.closed_at = now
            row.close_reason = "healthy"
    elif row is None:
        session.add(AssetQualityIncidentRow(target_table=policy.target_table, generation=policy.generation,
                                            kind=kind, reason=failure, opened_at=now, last_confirmed_at=now))
    else:
        row.last_confirmed_at = now
        row.reason = failure


async def supersede_quality_incidents(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, now: datetime, cause: AssetQualityIncidentClose,
) -> None:
    """Close ongoing old-generation incidents as superseded, not recovered."""
    await session.execute(update(AssetQualityIncidentRow).where(
        AssetQualityIncidentRow.target_table == policy.target_table,
        AssetQualityIncidentRow.generation == policy.generation, AssetQualityIncidentRow.closed_at.is_(None),
    ).values(closed_at=now, close_reason=cause))


async def acknowledge_quality_incident_locked(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, incident_id: UUID,
    expected_generation: int, actor_id: UUID, now: datetime, *, daily_only: bool = False,
    field_only: bool = False, min_rows_only: bool = False, weekly_only: bool = False,
) -> AssetQualityIncident:
    """Acknowledge once while keeping the condition active until evaluation recovers."""
    row = (await session.scalars(select(AssetQualityIncidentRow).where(
        AssetQualityIncidentRow.id == incident_id, AssetQualityIncidentRow.target_table == policy.target_table,
        _kind_filter(daily_only, field_only, min_rows_only, weekly_only),
    ))).one_or_none()
    if row is None:
        raise HTTPException(404, "Asset quality incident not found")
    if not policy.enabled or expected_generation != policy.generation or row.generation != expected_generation:
        raise HTTPException(409, "The quality policy changed; reload the incident list")
    if row.closed_at is not None:
        raise HTTPException(409, "The quality incident is no longer active")
    if row.acknowledged_at is not None:
        return _incident(row)
    row.acknowledged_at = now
    row.acknowledged_by = actor_id
    return _incident(row)

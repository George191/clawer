"""Persist transfer configuration without activating any pipeline or schedule."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.transfer_plans import (
    TransferCheckpointRow, TransferPlan, TransferPlanPayload, TransferPlanRow, TransferPreflight, TransferPreflightIssue,
)
from app.web.services.etl_layers import get_etl_layers


def _response(row: TransferPlanRow) -> TransferPlan:
    return TransferPlan(id=row.id, name=row.name, sourceSchema=row.source_schema, sourceTable=row.source_table,
                        targetSchema=row.target_schema, targetTable=row.target_table, writeMode=row.write_mode,
                        trigger=row.trigger, scheduleTime=row.schedule_time, scheduleWeekdayUtc=row.schedule_weekday_utc,
                        scheduleDayOfMonthUtc=row.schedule_day_of_month_utc,
                        incrementalCursor=row.incremental_cursor,
                        updatedAt=row.updated_at)


async def list_transfer_plans(session: AsyncSession, schema: str | None) -> list[TransferPlan]:
    statement = select(TransferPlanRow).order_by(TransferPlanRow.updated_at.desc(), TransferPlanRow.id)
    if schema is not None:
        statement = statement.where(or_(TransferPlanRow.source_schema == schema, TransferPlanRow.target_schema == schema))
    return [_response(row) for row in await session.scalars(statement)]


async def preflight_transfer_plan(payload: TransferPlanPayload) -> TransferPreflight:
    """Check a strict all-column mapping without reading or writing business rows."""
    source = (payload.sourceSchema, payload.sourceTable)
    target = (payload.targetSchema, payload.targetTable)
    endpoints = await get_etl_layers().transfer_endpoint_columns(source, target)
    return compare_transfer_endpoints(payload, endpoints)


def compare_transfer_endpoints(payload: TransferPlanPayload, endpoints: dict) -> TransferPreflight:
    source = (payload.sourceSchema, payload.sourceTable)
    target = (payload.targetSchema, payload.targetTable)
    issues: list[TransferPreflightIssue] = []

    def issue(code: str, message: str, column: str | None = None) -> None:
        issues.append(TransferPreflightIssue(code=code, message=message, column=column))

    if source not in endpoints:
        issue("source_unavailable", "Source is not a visible internal warehouse table.")
    if target not in endpoints:
        issue("target_unavailable", "Target is not a visible internal warehouse table.")
    if issues:
        return TransferPreflight(status="blocked", issues=issues, checkedColumns=0)

    source_cols = endpoints[source]["columns"]
    target_cols = endpoints[target]["columns"]
    if not source_cols or not target_cols:
        issue("no_columns", "Both tables must have columns for a strict mapping.")
    if payload.writeMode == "upsert" and not endpoints[target]["has_usable_pk"]:
        issue("target_primary_key_unusable", "Upsert requires a valid, non-deferrable target primary key.")
    if payload.incrementalCursor is not None:
        cursor = source_cols.get(payload.incrementalCursor)
        if cursor is None or not cursor["single_btree_pk"] or cursor["type_oid"] not in (20, 21, 23):
            issue("source_cursor_unusable", "Incremental Append requires a single-column, immediate btree integer primary key on the source.", payload.incrementalCursor)

    for name, dest in target_cols.items():
        if dest["generated"]:
            issue("target_generated", "Target is a computed generated column and cannot be written directly.", name)
        if dest["identity"] == "a":
            issue("target_identity_always", "Target is a GENERATED ALWAYS identity column; explicit values require an override.", name)
        origin = source_cols.get(name)
        if origin is None:
            issue("source_column_missing", "Target column is missing from the source.", name)
            continue
        if (origin["type_oid"], origin["type_modifier"]) != (dest["type_oid"], dest["type_modifier"]):
            issue("column_type_mismatch", f"Source type {origin['type']} does not match target type {dest['type']}.", name)
        if dest["not_null"] and not origin["not_null"]:
            issue("column_nullability_mismatch", "Source allows NULL but the target column is NOT NULL.", name)
    for name in source_cols:
        if name not in target_cols:
            issue("target_column_missing", "Source column is missing from the target.", name)

    return TransferPreflight(status="blocked" if issues else "compatible", issues=issues,
                             checkedColumns=len(source_cols.keys() & target_cols.keys()))


async def save_transfer_plan(
    session: AsyncSession, payload: TransferPlanPayload, plan_id: UUID | None = None
) -> TransferPlan:
    if payload.trigger == "weekly":
        from app.web.services.transfer_runs import weekly_transfer_enabled

        if not weekly_transfer_enabled():
            raise HTTPException(403, "Weekly UTC sandbox transfer execution is disabled")
    if payload.trigger == "monthly":
        from app.web.services.transfer_runs import monthly_transfer_enabled

        if not monthly_transfer_enabled():
            raise HTTPException(403, "Monthly UTC sandbox transfer execution is disabled")
    source = (payload.sourceSchema, payload.sourceTable)
    target = (payload.targetSchema, payload.targetTable)
    endpoints = await get_etl_layers().transfer_endpoints(source, target)
    if source not in endpoints:
        raise HTTPException(404, "Source is not a visible internal warehouse table")
    if target not in endpoints:
        raise HTTPException(404, "Target is not a visible internal warehouse table")
    if payload.writeMode == "upsert" and not endpoints[target]:
        raise HTTPException(422, "Upsert requires a primary key on the target table")

    fields = dict(name=payload.name, source_schema=source[0], source_table=source[1], target_schema=target[0],
                  target_table=target[1], write_mode=payload.writeMode, trigger=payload.trigger,
                  schedule_time=payload.scheduleTime, schedule_weekday_utc=payload.scheduleWeekdayUtc,
                  schedule_day_of_month_utc=payload.scheduleDayOfMonthUtc,
                  incremental_cursor=payload.incrementalCursor)
    if plan_id is None:
        row = TransferPlanRow(**fields, updated_at=datetime.now(timezone.utc))
        session.add(row)
    else:
        row = (await session.scalars(select(TransferPlanRow).where(TransferPlanRow.id == plan_id).with_for_update()
                                     .execution_options(populate_existing=True))).one_or_none()
        if row is None:
            raise HTTPException(404, "Transfer draft does not exist")
        if await session.get(TransferCheckpointRow, plan_id) is not None and any(
            getattr(row, name) != value for name, value in fields.items() if name != "name"
        ):
            raise HTTPException(409, "An incremental checkpoint exists. Its endpoints, cursor and execution mode are fixed.")
        for field, value in fields.items():
            setattr(row, field, value)
        row.updated_at = max(datetime.now(timezone.utc), row.updated_at + timedelta(microseconds=1))

    result = _response(row)
    await session.commit()
    return result

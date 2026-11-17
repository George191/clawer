"""Immutable mixed UTC+witness provenance shared by dispatch, worker, and history."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.transfer_upstream import TransferCommitEventRow, UpstreamMixedSlotRow, UpstreamTriggerJobRow
from app.web.core.config import settings
from app.web.services.upstream_rules import EVENT_WINDOW, positive_nested_events, valid_witness

SLOT_DECISION_WINDOW = timedelta(minutes=1)
SLOT_OBSERVATION_WINDOW = timedelta(minutes=5)
SLOT_INTERVAL = timedelta(days=1)


def mixed_upstream_enabled() -> bool:
    return ("ENVIRONMENT" in settings.model_fields_set and settings.ENVIRONMENT == "local"
            and settings.TRANSFER_SANDBOX_ENABLED and settings.TRANSFER_SANDBOX_UPSTREAM_ENABLED
            and settings.TRANSFER_SANDBOX_MIXED_UPSTREAM_ENABLED)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


async def mixed_db_now(session: AsyncSession) -> datetime:
    return (await session.scalar(text("SELECT pg_catalog.clock_timestamp()"))).astimezone(timezone.utc)


def mixed_lower_bound(binding, slot: datetime) -> datetime:
    return max(binding.updated_at, slot - SLOT_INTERVAL)


def mixed_rule_digest(binding, members, slot: datetime) -> str:
    members = sorted((str(member.parent_plan_id), _utc(member.parent_updated_at), member.cutoff_event_id)
                     for member in members)
    scope = [
        "upstream-daily-rule-v1", str(binding.plan_id), binding.generation, binding.mixed_time_utc,
        _utc(slot), _utc(binding.updated_at), binding.operator, binding.group_operator,
        [str(identity) for identity in binding.group_parent_ids or []], members,
        _utc(binding.child_updated_at), str(binding.armed_by), binding.physical_digest,
    ]
    return sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()


def mixed_witness_id(observed: UpstreamMixedSlotRow, descriptor) -> UUID:
    key = json.dumps([
        str(descriptor.plan_id), descriptor.generation, _utc(observed.slot), _utc(observed.observed_at),
        _utc(observed.decided_at), observed.rule_digest, observed.event_ids,
        [str(identity) for identity in observed.rule_parent_ids], descriptor.operator, descriptor.group_operator,
        [str(identity) for identity in descriptor.group_parent_ids or []],
        str(descriptor.armed_by), _utc(descriptor.child_updated_at),
    ], separators=(",", ":"))
    return uuid5(NAMESPACE_URL, "asiral:upstream-mixed:" + key)


async def exact_mixed_job(
    session: AsyncSession, job: UpstreamTriggerJobRow, events: list[TransferCommitEventRow],
) -> UpstreamMixedSlotRow | None:
    if job.mixed_slot is None:
        return None
    slot = await session.get(UpstreamMixedSlotRow, (job.plan_id, job.generation, job.mixed_slot))
    if (slot is None or slot.job_id != job.id or slot.observed_at is None or slot.decided_at is None
        or not slot.slot - SLOT_OBSERVATION_WINDOW <= slot.observed_at < slot.slot
        or not slot.slot <= slot.decided_at < slot.slot + SLOT_DECISION_WINDOW
        or job.expires_at != slot.slot + EVENT_WINDOW
        or not events or max(event.id for event in events) != job.event_id
        or slot.event_ids != sorted(event.id for event in events)
        or slot.rule_parent_ids is None or slot.rule_parent_ids != sorted(set(slot.rule_parent_ids))
        or not 1 <= len(slot.rule_parent_ids) <= 3
        or (job.group_operator is not None and job.rule_parent_ids != slot.rule_parent_ids)
        or any(not slot.slot - SLOT_INTERVAL < event.recorded_at <= slot.observed_at for event in events)
        or not valid_witness(job.operator, job.group_operator, job.group_parent_ids, slot.rule_parent_ids,
                             [event.parent_plan_id for event in events])
        or mixed_witness_id(slot, job) != job.id
        or not await positive_nested_events(session, events)):
        return None
    return slot

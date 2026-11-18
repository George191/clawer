"""Authorization, compatibility and publication rules for ontology revisions."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, text
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from app.web.api.models import Team, TeamUserJoin, Tenant, User
from app.web.api.models.ontology_registry import Definition, DefinitionIssue, OntologyDefinitionRow
from app.web.api.models.ontology_revisions import OntologyRevisionGrant, OntologyRevisionProposal, OntologyRevisionSnapshot
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord
from app.web.api.models.user import RoleTypes
from app.web.api.utils.models import StatusTypes
from app.web.services import ontology_registry as registry
from app.web.services.ontology_graph import graph_referenced_fields
from app.web.services.ontology_record_policy import validate_record_policy_refs
from app.web.services.ontology_registry_identity import eligible_principals
from app.web.services.ontology_registry_policy import definition_principals


@dataclass(frozen=True)
class Rights:
    edit: bool
    review: bool
    manage: bool
    warehouse: bool


def utcnow():
    return datetime.now(timezone.utc)


def dump(definition):
    return definition.model_dump(mode="json", by_alias=True)


def revision_snapshot(row, actor_id, proposal_id=None):
    """Store the exact committed publication head. Called in its committing transaction."""
    return OntologyRevisionSnapshot(
        definition_id=row.id,
        revision=row.revision,
        snapshot=row.published_definition,
        actor_id=actor_id,
        proposal_id=proposal_id,
        created_at=row.updated_at,
    )


def members(project_id):
    return (
        select(TeamUserJoin, User)
        .select_from(TeamUserJoin)
        .join(User, User.id == TeamUserJoin.user_id)
        .join(Team, Team.id == TeamUserJoin.team_id)
        .join(Tenant, Tenant.id == Team.tenant_id)
        .where(
            TeamUserJoin.team_id == project_id,
            TeamUserJoin.status == StatusTypes.ENABLE,
            Tenant.status == StatusTypes.ENABLE,
            User.status == StatusTypes.ENABLE,
        )
    )


def _manager(member, user):
    return user.is_superuser or member.role in {RoleTypes.ADMIN, RoleTypes.OWNER}


async def rights_for(session, project_id, actor_id, *, lock=False):
    statement = members(project_id).where(User.id == actor_id)
    if lock:
        statement = statement.with_for_update(read=True)
    result = (await session.execute(statement)).one_or_none()
    if result is None:
        return Rights(False, False, False, False)
    member, user = result
    grant = await session.scalar(select(OntologyRevisionGrant).where(OntologyRevisionGrant.membership_id == member.id))
    manager = _manager(member, user)
    return Rights(manager or bool(grant and grant.can_edit), manager or bool(grant and grant.can_review), manager, user.is_superuser)


async def authorized_type(session, actor, definition_id, *, need=None, lock=None):
    statement = select(OntologyDefinitionRow).where(
        OntologyDefinitionRow.id == definition_id,
        OntologyDefinitionRow.published_at.is_not(None),
    )
    if lock:
        statement = statement.with_for_update(read=lock == "read")
    row = await session.scalar(statement.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "Revision workspace not found")
    rights = await rights_for(session, row.project_id, actor.id, lock=bool(lock))
    if not (rights.edit or rights.review):
        raise HTTPException(404, "Revision workspace not found")
    current = Definition.model_validate(row.published_definition)
    if current.source.mode == "existing" and not rights.warehouse:
        raise HTTPException(404, "Revision workspace not found")
    if need and not getattr(rights, need):
        raise HTTPException(403, "Insufficient revision permissions")
    return row, current, rights


async def current_role_members(session, project_id, limit, offset):
    statement = (
        members(project_id)
        .add_columns(OntologyRevisionGrant)
        .outerjoin(OntologyRevisionGrant, OntologyRevisionGrant.membership_id == TeamUserJoin.id)
        .order_by(User.full_name, User.email, User.id)
        .offset(offset)
        .limit(limit + 1)
    )
    rows = (await session.execute(statement)).all()
    items = [
        {"id": user.id, "name": user.full_name or user.email, "manager": _manager(member, user),
         "canEdit": bool(grant and grant.can_edit), "canReview": bool(grant and grant.can_review)}
        for member, user, grant in rows[:limit]
    ]
    return {"items": items, "hasMore": len(rows) > limit}


async def set_member_roles(session, row, actor, target_id, payload):
    # Serialize grants against role reads and membership changes. Scope cannot be inferred from actor-supplied fields.
    await session.execute(select(Team.id).where(Team.id == row.project_id).with_for_update())
    rights = await rights_for(session, row.project_id, actor.id, lock=True)
    if not rights.manage:
        raise HTTPException(403, "Insufficient revision permissions")
    target = (await session.execute(
        members(row.project_id).where(User.id == target_id).with_for_update(read=True)
    )).one_or_none()
    if target is None:
        raise HTTPException(404, "Active team member not found")
    member, user = target
    if _manager(member, user):
        raise HTTPException(422, "Managers already have both revision roles")
    grant = await session.get(OntologyRevisionGrant, member.id)
    if payload.canEdit or payload.canReview:
        grant = grant or OntologyRevisionGrant(membership_id=member.id, can_edit=False, can_review=False)
        grant.can_edit = payload.canEdit
        grant.can_review = payload.canReview
        session.add(grant)
    elif grant:
        await session.delete(grant)
    await session.commit()


def definition_diff(before: dict, after: dict):
    """Provide property and action level comparisons without consulting runtime values."""
    before = before.copy()
    after = after.copy()
    changed = []
    for section, key in [("properties", "id"), ("actions", "kind")]:
        old = {item[key]: item for item in before.pop(section, [])}
        new = {item[key]: item for item in after.pop(section, [])}
        changed.extend({"path": f"{section}.{name}", "before": old.get(name), "after": new.get(name)}
                       for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name))
        if list(old) != list(new) and old.keys() == new.keys():
            changed.append({"path": f"{section}.order", "before": list(old), "after": list(new)})
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) != after.get(name):
            changed.append({"path": name, "before": before.get(name), "after": after.get(name)})
    return changed


def check_revision(row, expected):
    if row.revision != expected:
        raise HTTPException(409, "The published revision changed. Refresh before continuing.")
    if row.revision >= 2_147_483_646:
        raise HTTPException(409, "The published revision limit was reached")


def ensure_pending(row, proposal):
    check_revision(row, proposal.base_revision)
    if proposal.status != "pending":
        raise HTTPException(409, "This proposal has already been reviewed")


async def get_proposal(session, row, proposal_id, *, lock=False):
    statement = select(OntologyRevisionProposal).where(
        OntologyRevisionProposal.definition_id == row.id, OntologyRevisionProposal.id == proposal_id,
    )
    if lock:
        statement = statement.with_for_update()
    proposal = await session.scalar(statement)
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    return proposal


async def read_snapshot(session, row, revision):
    snapshot = await session.get(OntologyRevisionSnapshot, (row.id, revision))
    if snapshot is None:
        raise HTTPException(404, "Revision not found")
    return snapshot


def proposal_view(proposal, row, actor_id):
    return {
        "id": proposal.id, "baseRevision": proposal.base_revision, "restoreRevision": proposal.restore_revision,
        "summary": proposal.summary, "status": proposal.status, "authorId": proposal.author_id,
        "reviewerId": proposal.reviewer_id, "reviewNote": proposal.review_note, "reviewedAt": proposal.reviewed_at,
        "createdAt": proposal.created_at, "mergedRevision": proposal.merged_revision,
        "isAuthor": proposal.author_id == actor_id,
        "stale": row.revision != proposal.base_revision and proposal.status in {"pending", "approved"},
    }


@asynccontextmanager
async def _bound_table(row, current):
    source = current.source
    if source.mode == "none":
        yield None
        return
    if source.schema_name not in registry.WAREHOUSE_SCHEMAS or not registry.COLUMN.fullmatch(source.table or ""):
        raise HTTPException(409, "Invalid warehouse binding")
    relation = f"{registry.quote_identifier(source.schema_name)}.{registry.quote_identifier(source.table)}"
    async with registry.get_etl_layers().session() as warehouse:
        try:
            await warehouse.execute(text("SET LOCAL lock_timeout = '1500ms'"))
            await warehouse.execute(text(f"LOCK TABLE {relation} IN ACCESS SHARE MODE"))
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) in {"42P01", "3F000", "42501", "55P03"}:
                raise HTTPException(409, "The bound warehouse source changed or is unavailable") from exc
            raise
        table = await registry._warehouse_table(warehouse, source)
        registry.ensure_source_identity(row, current, table)
        yield table


def _check_structure(result, current, candidate):
    def reject(path, message):
        result.errors.append(DefinitionIssue(path=path, message=message))

    if (candidate.id, candidate.projectId) != (current.id, current.projectId):
        reject("id", "Project and object type identifiers are immutable")
    if candidate.source != current.source:
        reject("source", "The published source binding is immutable")
    if candidate.primaryKey != current.primaryKey:
        reject("primaryKey", "The published business key is immutable")
    old = {item.id: item for item in current.properties}
    new = {item.id: item for item in candidate.properties}
    for key in old.keys() & new.keys():
        if (old[key].type, old[key].sourceColumn) != (new[key].type, new[key].sourceColumn):
            reject(f"properties.{key}", "The storage type and source mapping of an existing property are immutable")
    removed = old.keys() - new.keys()
    required = {key for key, prop in new.items() if prop.required and (key not in old or not old[key].required)}
    return removed, required


async def _check_records(session, row, removed, required, result):
    if not removed and not required:
        return
    record = OntologyRuntimeRecord
    has_value = record.values.has_any(array(sorted(removed))) if removed else None
    missing = [record.values[key].astext.is_(None) for key in sorted(required)]
    conditions = ([has_value] if has_value is not None else []) + missing
    try:
        async with session.begin_nested():
            await session.execute(text("SET LOCAL statement_timeout = '2500ms'"))
            incompatible = await session.scalar(select(record.id).where(
                record.definition_id == row.id, or_(*conditions),
            ).limit(1))
            await session.execute(text("SET LOCAL statement_timeout TO DEFAULT"))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "57014":
            raise HTTPException(409, "The compatibility check exceeded its execution budget") from exc
        raise
    if incompatible is not None:
        result.errors.append(DefinitionIssue(path="properties", message="Existing records contain removed fields or lack a newly required value"))


async def validate_change(session, row, current, candidate, source):
    eligible = await eligible_principals(
        session, row.project_id, definition_principals(candidate.actions, candidate.security),
    )
    result = registry.assess_definition(candidate, source, eligible=eligible)
    removed, required = _check_structure(result, current, candidate)
    if dump(current) == dump(candidate):
        result.errors.append(DefinitionIssue(path="definition", message="The proposed definition has no changes"))
    if not result.errors:
        for error in await validate_record_policy_refs(session, row.id, candidate):
            result.errors.append(DefinitionIssue(path="security.recordRules", message=error))
        if removed and removed & await graph_referenced_fields(session, row.id):
            result.errors.append(DefinitionIssue(path="properties", message="Remove references to these properties from saved object sets before removing them"))
        if candidate.source.mode == "none":
            await _check_records(session, row, removed, required, result)
    if result.errors:
        raise HTTPException(422, dump(result))
    return result


async def create_proposal(session, row, current, actor, revision, candidate, summary, restore_revision=None):
    check_revision(row, revision)
    async with _bound_table(row, current) as source:
        await validate_change(session, row, current, candidate, source)
        proposal = OntologyRevisionProposal(
            definition_id=row.id, base_revision=row.revision, author_id=actor.id, summary=summary,
            proposed_definition=dump(candidate), restore_revision=restore_revision,
        )
        session.add(proposal)
        await session.flush()
        view = proposal_view(proposal, row, actor.id)
        await session.commit()
    return view


async def review_proposal(session, row, proposal, actor, payload):
    ensure_pending(row, proposal)
    if actor.id == proposal.author_id:
        raise HTTPException(403, "A different authorized member must review this proposal")
    proposal.status = "approved" if payload.approve else "rejected"
    proposal.reviewer_id = actor.id
    proposal.reviewed_at = utcnow()
    proposal.review_note = payload.note
    view = proposal_view(proposal, row, actor.id)
    await session.commit()
    return view


async def merge_proposal(session, row, current, proposal, actor):
    check_revision(row, proposal.base_revision)
    if proposal.status != "approved" or proposal.author_id == proposal.reviewer_id:
        raise HTTPException(409, "This proposal requires independent approval")
    author = await rights_for(session, row.project_id, proposal.author_id, lock=True)
    reviewer = await rights_for(session, row.project_id, proposal.reviewer_id, lock=True)
    if not author.edit or not reviewer.review or (current.source.mode == "existing" and not (author.warehouse and reviewer.warehouse)):
        raise HTTPException(409, "The proposer or reviewer no longer has permission")
    candidate = Definition.model_validate(proposal.proposed_definition)
    async with _bound_table(row, current) as source:
        await validate_change(session, row, current, candidate, source)
        row.revision += 1
        row.updated_at = utcnow()
        row.published_definition = dump(candidate)
        if source:
            row.source_signature = registry.source_signature(candidate, source)
        proposal.status = "merged"
        proposal.merged_revision = row.revision
        session.add(revision_snapshot(row, actor.id, proposal.id))
        view = proposal_view(proposal, row, actor.id)
        await session.commit()
    return view

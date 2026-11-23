"""Read-only API keys delegate a frozen range of the publisher's current access."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.web.api.models import User
from app.web.api.models.ontology_explorer import ExplorerQuery
from app.web.api.models.ontology_publications import OntologyPublication
from app.web.api.utils.models import StatusTypes
from app.web.services.ontology_action_runtime import _definition_access
from app.web.services.ontology_explorer import evaluate, validate_query
from app.web.services.ontology_revisions import rights_for


PUBLICATION_DATA_PATH = "/ontology/v1/publications/{publication_id}/data"


def view(row):
    return {"id": row.id, "name": row.name, "revision": row.revision, "fields": row.fields,
            "query": row.query, "validFrom": row.valid_from, "validUntil": row.valid_until,
            "version": row.version, "keyStatus": row.key_status,
            "url": PUBLICATION_DATA_PATH.format(publication_id=row.id)}


async def management_access(session, user, definition_id):
    grant = await _definition_access(session, user, definition_id)
    if not (await rights_for(session, grant[0].project_id, user.id, lock=True)).manage:
        raise HTTPException(403, "Only project managers can publish an API")
    return grant


async def get_publication(session, definition_id, publication_id):
    row = await session.scalar(select(OntologyPublication).where(
        OntologyPublication.id == publication_id, OntologyPublication.definition_id == definition_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "Publication not found")
    return row


async def save(session, user, definition_id, payload, publication_id=None):
    grant = await management_access(session, user, definition_id)
    if payload.modelRevision != grant[0].revision:
        raise HTTPException(409, "The object model changed. Refresh before publishing.")
    if not set(payload.fields).issubset(grant[3]):
        raise HTTPException(403, "Publication includes unavailable properties")
    validate_query(grant, payload.query)
    query = payload.query.model_dump(mode="json")
    if len(json.dumps(query).encode()) > 65536:
        raise HTTPException(422, "Publication range is too large")
    # Exercise the actual query before issuing a grant; no DDL or data writes occur here.
    await evaluate(session, user, definition_id, payload.query, 1, 0)
    if publication_id is None:
        row = OntologyPublication(definition_id=definition_id, owner_id=user.id, name=payload.name,
            revision=grant[0].revision, fields=payload.fields, query=query,
            valid_from=payload.validFrom, valid_until=payload.validUntil)
        session.add(row)
    else:
        row = await get_publication(session, definition_id, publication_id)
        if payload.version != row.version:
            raise HTTPException(409, "Publication changed. Refresh before saving.")
        row.owner_id, row.name, row.revision = user.id, payload.name, grant[0].revision
        row.fields, row.query = payload.fields, query
        row.valid_from, row.valid_until = payload.validFrom, payload.validUntil
        row.version = uuid4()
    await session.flush()
    result = view(row)
    await session.commit()
    return result


async def change_key(session, user, definition_id, publication_id, revoke):
    await management_access(session, user, definition_id)
    row = await get_publication(session, definition_id, publication_id)
    key = None if revoke else "pk_live_" + secrets.token_urlsafe(32)
    row.key_digest = hashlib.sha256(key.encode()).hexdigest() if key else None
    row.key_status = "revoked" if revoke else "active"
    row.version = uuid4()
    result = {"id": row.id, "keyStatus": row.key_status, **({"key": key} if key else {})}
    await session.commit()
    return result


async def read(session, publication_id, key, limit, offset):
    if not key or len(key) > 256:
        raise HTTPException(401, "X-API-Key is required")
    row = await session.scalar(select(OntologyPublication).where(
        OntologyPublication.id == publication_id,
    ))
    now = datetime.now(timezone.utc)
    if (row is None or row.key_status != "active" or not row.key_digest
            or not hmac.compare_digest(row.key_digest, hashlib.sha256(key.encode()).hexdigest())
            or not row.valid_from <= now < row.valid_until):
        raise HTTPException(404, "Publication is unavailable or outside its validity window")
    owner = await session.get(User, row.owner_id)
    if owner is None or owner.status != StatusTypes.ENABLE:
        raise HTTPException(404, "Publication is unavailable")
    version = row.version
    grant = await _definition_access(session, owner, row.definition_id)
    # Match management's definition -> publication lock order, then recheck the grant.
    row = await session.scalar(select(OntologyPublication).where(
        OntologyPublication.id == publication_id,
    ).with_for_update(read=True).execution_options(populate_existing=True))
    if row is None or row.version != version:
        raise HTTPException(404, "Publication changed during the read")
    if grant[0].revision != row.revision:
        raise HTTPException(409, "The object model changed. The publisher must review and save this publication again.")
    if not (await rights_for(session, grant[0].project_id, owner.id, lock=True)).manage:
        raise HTTPException(404, "Publication is unavailable")
    if not set(row.fields).issubset(grant[3]):
        raise HTTPException(404, "Published properties are no longer available")
    result = await evaluate(session, owner, row.definition_id, ExplorerQuery.model_validate(row.query), limit, offset)
    if datetime.now(timezone.utc) >= row.valid_until:
        raise HTTPException(404, "Publication expired during the read")
    return {"items": [{field: record["properties"].get(field) for field in row.fields} for record in result["items"]],
            "hasMore": result["hasMore"], "nextOffset": offset + limit if result["hasMore"] else None}

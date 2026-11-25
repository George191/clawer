"""Stored logical links between safely addressed warehouse rows."""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from app.storage.postgres_client import get_pg_client

from app.etl.schema.object_links import canonical_object_key, object_key_hash
from app.web.services.etl_layers import _object_schema, get_etl_layers, row_identity_sql
from app.web.services.explorer_rows import _asset, _limits, _source


async def require_object_links(session):
    if not await session.scalar(text("""SELECT
        to_regclass('app.ontology_object_links') IS NOT NULL AND
        to_regclass('app.ontology_object_relations') IS NOT NULL""")):
        raise HTTPException(503, "Logical object links are not initialized")


async def _prepare(session, catalog_session):
    await _limits(session)
    await _limits(catalog_session)
    await catalog_session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await catalog_session.execute(text("SET LOCAL DateStyle = 'ISO, YMD'"))
    await require_object_links(session)


async def _key(session, asset, key, *, lock=False):
    record = await _source(session, asset, key, row_identity_sql(asset["primary"]), lock=lock)
    return record[0]


def _choice(relation, direction, peer):
    left, right = relation["source_fields"], relation["target_fields"]
    if direction == "incoming":
        left, right = right, left
    return {"id": str(relation["id"]) + ":" + direction, "relationId": str(relation["id"]), "name": relation["name"],
            "direction": direction, "kind": "logical", "schema": peer[0], "table": peer[1],
            "fields": left, "targetFields": right}


async def read_row_links(layer, table, query, *, can_manage=False):
    schema = _object_schema(layer)
    async with get_etl_layers().session(repeatable_read=True, schema=schema, table=table) as catalog_session, get_pg_client().session() as session:
        await _prepare(session, catalog_session)
        source = await _asset(catalog_session, schema, table)
        key = await _key(catalog_session, source, query.key)
        relations = (await session.execute(text("""SELECT * FROM app.ontology_object_relations
            WHERE enabled AND ((source_schema=:schema AND source_table=:table)
                OR (target_schema=:schema AND target_table=:table)) ORDER BY name, id"""),
            {"schema": schema, "table": table})).mappings().all()
        choices, assets = [], {(schema, table): source}
        for relation in relations:
            for side, other, direction in (("source", "target", "outgoing"), ("target", "source", "incoming")):
                if (relation[side + "_schema"], relation[side + "_table"]) != (schema, table):
                    continue
                peer = (relation[other + "_schema"], relation[other + "_table"])
                if peer not in assets:
                    try:
                        assets[peer] = await _asset(catalog_session, *peer)
                    except HTTPException as failure:
                        if failure.status_code != 404:
                            raise
                        assets[peer] = None
                if assets[peer] is not None:
                    choices.append({**_choice(relation, direction, peer), "canLink": can_manage})
        selected = next((item for item in choices if item["id"] == query.relation), None) if query.relation else next(iter(choices), None)
        if query.relation and not selected:
            raise HTTPException(404, "Relationship not available")
        result = {"relations": choices, "selected": selected["id"] if selected else None, "canManage": can_manage,
                  "identities": [], "links": [], "hasMore": False, "offset": query.offset}
        if not selected:
            return result
        target = assets[(selected["schema"], selected["table"])]
        if not target["primary"]:
            raise HTTPException(409, "The related table has no primary key for a stable record address")
        side, other = ("source", "target") if selected["direction"] == "outgoing" else ("target", "source")
        edges = (await session.execute(text(f"""SELECT id, version, {other}_key AS identity
            FROM app.ontology_object_links WHERE relation_id=:relation AND removed_at IS NULL
              AND {side}_hash=:hash AND {side}_key=CAST(:key AS jsonb)
            ORDER BY id LIMIT :limit OFFSET :offset"""),
            {"relation": int(selected["relationId"]), "hash": object_key_hash(key),
             "key": canonical_object_key(key), "limit": query.limit + 1, "offset": query.offset})).mappings().all()
        links = []
        for edge in edges[:query.limit]:
            identity = await _key(catalog_session, target, edge["identity"])
            if identity != edge["identity"]:
                raise HTTPException(409, "The related object key changed; review the logical relationship")
            links.append({"linkId": str(edge["id"]), "version": str(edge["version"]),
                          "identity": identity, "canUnlink": can_manage})
        return {**result, "links": links, "identities": [item["identity"] for item in links], "hasMore": len(edges) > query.limit}


async def mutate_row_link(layer, table, payload, user, *, link_id=None):
    if not user.is_superuser:
        raise HTTPException(403, "Physical object management permission is required")
    from app.web.services.etl_workspace import get_etl_workspace

    schema = _object_schema(layer)
    async with get_etl_workspace()._session() as session, get_etl_workspace()._session(
        business=True, schema=schema, table=table,
    ) as catalog_session:
        await _prepare(session, catalog_session)
        relation = (await session.execute(text("""SELECT * FROM app.ontology_object_relations
            WHERE id=:id AND enabled FOR UPDATE"""), {"id": int(payload.relationId)})).mappings().first()
        if not relation:
            raise HTTPException(404, "Relationship not available")
        anchor_side = "source" if payload.direction == "outgoing" else "target"
        if (relation[anchor_side + "_schema"], relation[anchor_side + "_table"]) != (schema, table):
            raise HTTPException(404, "Relationship not available")
        entries = []
        for side in ("source", "target"):
            address = (relation[side + "_schema"], relation[side + "_table"])
            asset = await _asset(catalog_session, *address)
            raw = payload.key if side == anchor_side else payload.targetKey
            identity = await _key(catalog_session, asset, raw)
            entries.append((address, canonical_object_key(identity), side, asset, identity))
        keys = {}
        for _, _, side, asset, identity in sorted(entries, key=lambda item: (item[0], item[1])):
            keys[side] = await _key(catalog_session, asset, identity, lock=True)
        params = {"relation": relation["id"], "source_hash": object_key_hash(keys["source"]),
                  "target_hash": object_key_hash(keys["target"]), "source_key": canonical_object_key(keys["source"]),
                  "target_key": canonical_object_key(keys["target"]), "actor": str(user.id)}
        existing = (await session.execute(text("""SELECT * FROM app.ontology_object_links
            WHERE relation_id=:relation AND source_hash=:source_hash AND target_hash=:target_hash FOR UPDATE"""), params)).mappings().first()
        if existing and (existing["source_key"] != keys["source"] or existing["target_key"] != keys["target"]):
            raise HTTPException(409, "Stored relationship identity conflicts with the selected objects")
        if link_id is not None:
            if not existing or existing["removed_at"] is not None or str(existing["id"]) != str(link_id) or existing["version"] != int(payload.expectedVersion):
                raise HTTPException(409, "Relationship changed or was removed; reload before trying again")
            await session.execute(text("""UPDATE app.ontology_object_links SET removed_at=now(),
                removed_by=CAST(:actor AS uuid), updated_at=now(), version=version+1 WHERE id=CAST(:id AS uuid)"""),
                {**params, "id": str(link_id)})
            return {"linkId": str(link_id), "version": str(existing["version"] + 1), "removed": True}
        if existing and existing["removed_at"] is None:
            raise HTTPException(409, "These objects are already linked")
        occupied = []
        if relation["cardinality"] in ("one_to_one", "many_to_one"):
            occupied.append("(source_hash=:source_hash AND source_key=CAST(:source_key AS jsonb))")
        if relation["cardinality"] in ("one_to_one", "one_to_many"):
            occupied.append("(target_hash=:target_hash AND target_key=CAST(:target_key AS jsonb))")
        if occupied and await session.scalar(text("""SELECT EXISTS(SELECT 1 FROM app.ontology_object_links
            WHERE relation_id=:relation AND removed_at IS NULL AND (""" + " OR ".join(occupied) + "))"), params):
            raise HTTPException(409, "Relationship would exceed its declared cardinality")
        params["id"] = str(uuid4())
        if existing:
            await session.execute(text("""UPDATE app.ontology_object_links SET id=CAST(:id AS uuid),
                version=version+1, created_at=now(), updated_at=now(), created_by=CAST(:actor AS uuid),
                removed_at=NULL, removed_by=NULL WHERE relation_id=:relation
                AND source_hash=:source_hash AND target_hash=:target_hash"""), params)
            version = existing["version"] + 1
        else:
            await session.execute(text("""INSERT INTO app.ontology_object_links
                (id, relation_id, source_key, target_key, source_hash, target_hash, created_by)
                VALUES (CAST(:id AS uuid), :relation, CAST(:source_key AS jsonb), CAST(:target_key AS jsonb),
                    :source_hash, :target_hash, CAST(:actor AS uuid))"""), params)
            version = 1
        return {"linkId": params["id"], "version": str(version), "removed": False}


async def protect_linked_row(session, schema, table, key, *, catalog_session):
    """Serialize deletion/key edits with link creation, then reject live references."""
    await _prepare(session, catalog_session)
    asset = await _asset(catalog_session, schema, table)
    try:
        identity = await _key(catalog_session, asset, key, lock=True)
    except HTTPException as failure:
        if failure.status_code != 404:
            raise
        raise HTTPException(409, {"code": "row_version_conflict",
                                  "message": "Row changed or was deleted. Reload the preview before saving."}) from failure
    linked = await session.scalar(text("""SELECT EXISTS (
        SELECT 1 FROM app.ontology_object_links l JOIN app.ontology_object_relations r ON r.id=l.relation_id
        WHERE l.removed_at IS NULL AND (
            (r.source_schema=:schema AND r.source_table=:table AND l.source_hash=:hash AND l.source_key=CAST(:key AS jsonb)) OR
            (r.target_schema=:schema AND r.target_table=:table AND l.target_hash=:hash AND l.target_key=CAST(:key AS jsonb))))"""),
        {"schema": schema, "table": table, "hash": object_key_hash(identity), "key": canonical_object_key(identity)})
    if linked:
        raise HTTPException(409, "Remove relationships before deleting this record or changing its primary key")

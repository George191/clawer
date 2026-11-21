"""Read Published object details without crossing into physical Explorer authorization."""

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.web.api.models.ontology_explorer import ExplorerQuery
from app.web.services.explorer_files import record_file_link, render_html
from app.web.services.ontology_action_runtime import _definition_access
from app.web.services.ontology_explorer import evaluate


async def record(session, user, definition_id, identity):
    result = await evaluate(session, user, definition_id, ExplorerQuery(keys=[identity]), 1, 0)
    if result["count"] != 1 or not result["items"]:
        raise HTTPException(404, "Object not found")
    return {**result["items"][0], "actions": result["actions"]}


async def values_for_field(session, user, definition_id, identity, field):
    _, _, _, fields = await _definition_access(session, user, definition_id)
    if field not in fields:
        raise HTTPException(403, "Object property is unavailable")
    return (await record(session, user, definition_id, identity))["properties"]


async def file(session, user, definition_id, identity, field, *, download, path):
    values = await values_for_field(session, user, definition_id, identity, field)
    return await record_file_link(values, field, download=download, path=path)


async def html(session, user, definition_id, identity, field):
    values = await values_for_field(session, user, definition_id, identity, field)
    return await run_in_threadpool(render_html, values, field)

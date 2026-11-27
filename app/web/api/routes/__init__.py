from fastapi import APIRouter, Depends
from agent.api import router as agent_router
from agent.management import router as agent_management_router

from app.web.api.dependencies.common import get_current_user

from . import (
    login, user, utils, apikey, team, tenant, graph, model,
    provider, thread, upload, dataset, embedding,
    health, dashboard, templates, tasks, ai_collect, automation,
    monitor, explorer_layouts, explorer_properties, explorer_saved_items,
    etl, infrastructure, scheduler, cdc, scout_chats,
)
from app.web.core.config import settings


api_router = APIRouter()

api_router.include_router(login.router)
api_router.include_router(user.router)
api_router.include_router(utils.router)
api_router.include_router(apikey.router)
api_router.include_router(team.router)
api_router.include_router(tenant.router)
api_router.include_router(graph.router)
api_router.include_router(model.router)
api_router.include_router(provider.router)
api_router.include_router(thread.router)
api_router.include_router(upload.router)
api_router.include_router(dataset.router)
api_router.include_router(embedding.router)
api_router.include_router(health.router)
api_router.include_router(dashboard.router)
api_router.include_router(templates.router)
api_router.include_router(tasks.router)
api_router.include_router(ai_collect.router)
api_router.include_router(scout_chats.router)
api_router.include_router(agent_router)
api_router.include_router(agent_management_router)
api_router.include_router(automation.router)
api_router.include_router(scheduler.router)
api_router.include_router(monitor.router)
api_router.include_router(explorer_layouts.router)
api_router.include_router(explorer_properties.router)
api_router.include_router(explorer_saved_items.router)
api_router.include_router(etl.router)
api_router.include_router(infrastructure.router)
api_router.include_router(cdc.router)

if settings.ENVIRONMENT == "local":
    ...

from fastapi import APIRouter, Depends

from app.web.api.dependencies.common import get_current_user

from . import (
    login, user, utils, apikey, team, graph, model,
    provider, thread, upload, dataset, embedding,
    health, dashboard, templates, tasks, ai_collect, automation,
    monitor,
    etl, infrastructure, scheduler,
)
from app.web.core.config import settings


api_router = APIRouter()

api_router.include_router(login.router)
api_router.include_router(user.router)
api_router.include_router(utils.router)
api_router.include_router(apikey.router)
api_router.include_router(team.router)
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
api_router.include_router(automation.router)
api_router.include_router(scheduler.router)
api_router.include_router(monitor.router)
api_router.include_router(etl.router)
api_router.include_router(infrastructure.router)

if settings.ENVIRONMENT == "local":
    ...

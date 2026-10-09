import sentry_sdk

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from starlette.middleware.cors import CORSMiddleware

from app.web.api.routes import api_router
from app.web.api.routes.health import health as health_check
from app.web.api.routes.task_socket import router as task_socket_router
from app.web.core.config import settings
from app.web.core.exceptions import register_exception_handlers
from app.web.core.middleware import register_middleware
from fastapi_pagination import add_pagination as register_pagination


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.ENVIRONMENT != "local":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
)

# Set all CORS enabled origins
if settings.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.all_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

register_exception_handlers(app)
register_middleware(app)
register_pagination(app)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(task_socket_router)
# Keep the deployment health-check contract compatible with the existing
# Docker and README references while the business API uses /api/v1.
app.add_api_route(
    "/api/health",
    health_check,
    methods=["GET"],
    tags=["health"],
    name="legacy_health",
)

app.mount("/", StaticFiles(directory="static", html=True), name="frontend")

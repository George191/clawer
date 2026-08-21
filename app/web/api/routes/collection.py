"""数据中台业务模块路由聚合。

这些模块复用现有任务、ETL、AI 和调度实现，但以平台级能力平行开放，
不把任务或 AI 能力限制为采集系统的子模块。
"""

from fastapi import APIRouter, Depends

from app.web.api.dependencies.common import get_current_user
from app.web.routes.ai_collect import router as ai_collect_router
from app.web.routes.dashboard import router as dashboard_router
from app.web.routes.etl import router as etl_router
from app.web.routes.monitor import router as monitor_router
from app.web.routes.scheduler import router as scheduler_router
from app.web.routes.socket import router as socket_router
from app.web.routes.tasks import router as tasks_router
from app.web.routes.automation import router as automation_router
from app.web.routes.templates import router as templates_router


router = APIRouter(
    dependencies=[Depends(get_current_user)],
)
router.include_router(dashboard_router, tags=["dashboard"])
router.include_router(etl_router, tags=["etl"])
router.include_router(tasks_router, tags=["tasks"])
router.include_router(automation_router, tags=["automation"])
router.include_router(templates_router, tags=["template"])
router.include_router(monitor_router, tags=["monitor"])
router.include_router(ai_collect_router, tags=["ai"])
router.include_router(scheduler_router, tags=["scheduler"])
router.include_router(socket_router, tags=["socket"])

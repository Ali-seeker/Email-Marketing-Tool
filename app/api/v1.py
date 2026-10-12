"""API v1 master router aggregating all domain endpoints."""

from fastapi import APIRouter, Depends

from app.api.approval import router as approval_router
from app.api.campaigns import router as campaigns_router
from app.api.deps import verify_api_token
from app.api.emails import router as emails_router
from app.api.enrollment import router as enrollment_router
from app.api.events import router as events_router
from app.api.leads import router as leads_router
from app.api.planning import router as planning_router
from app.api.replies import router as replies_router
from app.api.safety import router as safety_router
from app.api.settings import router as settings_router
from app.api.stats import router as stats_router
from app.api.suppression import router as suppression_router
from app.api.templates import router as templates_router

# Master v1 router protected by X-API-Token verification
api_v1_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(verify_api_token)],
)

# Mount domain sub-routers
api_v1_router.include_router(campaigns_router)
api_v1_router.include_router(leads_router)
api_v1_router.include_router(approval_router)
api_v1_router.include_router(enrollment_router)
api_v1_router.include_router(planning_router)
api_v1_router.include_router(emails_router)
api_v1_router.include_router(replies_router)
api_v1_router.include_router(templates_router)
api_v1_router.include_router(suppression_router)
api_v1_router.include_router(stats_router)
api_v1_router.include_router(safety_router)
api_v1_router.include_router(settings_router)
api_v1_router.include_router(events_router)

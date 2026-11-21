from fastapi import APIRouter

from app.api.v1 import analytics, auth, dashboard, urls

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(urls.router)
api_router.include_router(analytics.router)
api_router.include_router(dashboard.router)

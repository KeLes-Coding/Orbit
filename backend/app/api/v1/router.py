from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.files import router as files_router
from app.api.v1.health import router as health_router
from app.api.v1.llm_configs import router as llm_configs_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(llm_configs_router)
api_router.include_router(files_router)
api_router.include_router(conversations_router)
api_router.include_router(health_router)

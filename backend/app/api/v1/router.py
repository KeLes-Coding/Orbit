from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.health import router as health_router
from app.api.v1.llm_configs import router as llm_configs_router

api_router = APIRouter()
# v1 版本的总路由入口，后续 auth / llm_configs / conversations 都从这里注册。
api_router.include_router(auth_router)
api_router.include_router(llm_configs_router)
api_router.include_router(conversations_router)
api_router.include_router(health_router)

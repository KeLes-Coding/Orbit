from fastapi import APIRouter

from app.api.v1.health import router as health_router

api_router = APIRouter()
# v1 版本的总路由入口，后续 auth / llm_configs / conversations 都从这里注册。
api_router.include_router(health_router)

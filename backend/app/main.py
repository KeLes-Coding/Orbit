from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.schema_guard import run_startup_schema_guard


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 即使直接运行 uvicorn，也在启动时先做一次 schema 护栏检查。
    await run_startup_schema_guard()
    yield


def create_app() -> FastAPI:
    # 应用工厂：后续测试或脚本可以复用这里创建独立 FastAPI 实例。
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    # 前后端分离开发时，前端 dev server 需要跨域访问后端 API。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 所有业务 API 统一挂到 /api/v1，便于后续版本演进。
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()

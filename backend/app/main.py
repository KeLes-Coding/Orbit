import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.schema_guard import run_startup_schema_guard

logger = logging.getLogger(__name__)


async def _cleanup_expired_files_loop() -> None:
    while True:
        await asyncio.sleep(3600)  # Every hour
        try:
            from app.db.session import AsyncSessionLocal
            from app.repositories.conversation_file import ConversationFileRepository

            async with AsyncSessionLocal() as session:
                repo = ConversationFileRepository(session)
                count = await repo.delete_expired()
                if count:
                    await session.commit()
                    logger.info("Cleaned up %d expired pending files", count)
        except Exception:
            logger.exception("Failed to clean up expired files")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_startup_schema_guard()
    # 强制解析一次存储根路径并缓存，避免后台 task CWD 漂移。
    from app.services.files.storage import _resolve_storage_root
    _resolve_storage_root()
    cleanup_task = asyncio.create_task(_cleanup_expired_files_loop())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


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

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.config import settings
from app.db.session import engine

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI_PATH = BACKEND_DIR / "alembic.ini"


def _build_alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI_PATH))
    # 启动检查时显式写回数据库地址，避免依赖当前工作目录或外部 shell 状态。
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


async def get_pending_head_revisions() -> tuple[list[str], list[str]]:
    # 读取数据库当前 revision，并与本地 Alembic head 对比，判断是否存在漏迁移。
    config = _build_alembic_config()
    script = ScriptDirectory.from_config(config)
    heads = list(script.get_heads())

    async with engine.connect() as connection:
        try:
            result = await connection.execute(text("select version_num from alembic_version"))
            current = [row[0] for row in result.fetchall()]
        except (ProgrammingError, DBAPIError):
            # 新库还没初始化迁移表时，视为“当前没有任何 revision”。
            current = []

    pending = [revision for revision in heads if revision not in current]
    return current, pending


async def run_startup_schema_guard() -> None:
    # 应用启动时先做 schema 护栏，避免运行到业务路径才暴露“缺列”这类错误。
    if not settings.schema_check_on_startup:
        return

    if settings.auto_migrate_on_startup:
        await _run_alembic_upgrade_head()

    current, pending = await get_pending_head_revisions()
    if not pending:
        return

    current_label = ", ".join(current) if current else "none"
    pending_label = ", ".join(pending)
    raise RuntimeError(
        "数据库 schema 不是最新版本。"
        f" current={current_label}; pending={pending_label}。"
        " 请先执行 `cd backend && ../Orbit/bin/python -m alembic upgrade head`，"
        " 或开启 `ORBIT_AUTO_MIGRATE_ON_STARTUP=true`。"
    )


async def _run_alembic_upgrade_head() -> None:
    # Alembic 的 env.py 会自行管理迁移连接；这里用子进程执行，避免和当前事件循环冲突。
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        cwd=str(BACKEND_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0:
        return

    raise RuntimeError(
        "启动时自动迁移失败。"
        f"\nstdout:\n{stdout.decode().strip()}"
        f"\nstderr:\n{stderr.decode().strip()}"
    )

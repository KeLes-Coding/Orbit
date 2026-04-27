from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Alembic 通过导入这里拿到全部模型元数据，自动迁移和离线 SQL 都依赖这个入口。
from app.models import Conversation, LLMConfig, Message, User  # noqa: E402,F401

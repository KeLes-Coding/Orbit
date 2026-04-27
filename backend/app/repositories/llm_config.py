from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_config import LLMConfig


class LLMConfigRepository:
    # Repository 保持薄层，只负责 llm_configs 表的数据库操作。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self, user_id: UUID) -> list[LLMConfig]:
        # 默认配置排在最前面，方便前端直接展示当前默认模型。
        statement = (
            select(LLMConfig)
            .where(LLMConfig.user_id == user_id, LLMConfig.archived_at.is_(None))
            .order_by(LLMConfig.is_default.desc(), LLMConfig.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def count_active(self, user_id: UUID) -> int:
        # 创建第一条配置时用它判断是否需要自动设为默认。
        statement = select(func.count()).select_from(LLMConfig).where(
            LLMConfig.user_id == user_id,
            LLMConfig.archived_at.is_(None),
        )
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    async def get_active(self, *, user_id: UUID, config_id: UUID) -> LLMConfig | None:
        # 所有单条读取都带 user_id，防止跨租户访问模型配置。
        statement = select(LLMConfig).where(
            LLMConfig.id == config_id,
            LLMConfig.user_id == user_id,
            LLMConfig.archived_at.is_(None),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_active_by_name(self, *, user_id: UUID, name: str) -> LLMConfig | None:
        # 用于服务层提前给出友好的“名称已存在”错误。
        statement = select(LLMConfig).where(
            LLMConfig.user_id == user_id,
            LLMConfig.name == name,
            LLMConfig.archived_at.is_(None),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def unset_defaults(self, user_id: UUID) -> None:
        # 部分唯一索引要求同一用户只能有一个活跃默认配置，因此先清空旧默认。
        await self.session.execute(
            update(LLMConfig)
            .where(
                LLMConfig.user_id == user_id,
                LLMConfig.archived_at.is_(None),
                LLMConfig.is_default.is_(True),
            )
            .values(is_default=False, updated_at=func.now())
        )

    async def create(
        self,
        *,
        user_id: UUID,
        name: str,
        provider: str,
        model: str,
        base_url: str | None,
        api_key_ciphertext: str | None,
        provider_options: dict,
        is_default: bool,
    ) -> LLMConfig:
        # flush 后拿到数据库生成的 UUID，refresh 后拿到服务端默认字段。
        config = LLMConfig(
            user_id=user_id,
            name=name,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key_ciphertext=api_key_ciphertext,
            provider_options=provider_options,
            is_default=is_default,
        )
        self.session.add(config)
        await self.session.flush()
        await self.session.refresh(config)
        return config

    async def archive(self, config: LLMConfig) -> None:
        # 软删除配置，同时取消默认状态，避免归档配置继续被新会话选中。
        config.archived_at = datetime.now(timezone.utc)
        config.is_default = False

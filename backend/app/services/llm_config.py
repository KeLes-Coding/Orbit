from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.models.llm_config import LLMConfig
from app.repositories.llm_config import LLMConfigRepository
from app.schemas.llm_config import (
    LLMConfigCreate,
    LLMConfigRead,
    LLMConfigUpdate,
    LLMModelProbe,
    LLMModelRead,
    LLMProviderRead,
)
from app.services.llm.providers.base import LLMProviderError, LLMRuntimeConfig
from app.services.llm.providers.registry import get_provider, list_provider_infos


class LLMConfigService:
    # 模型配置服务负责用户维度的配置隔离、默认配置切换和 API Key 加密。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.configs = LLMConfigRepository(session)

    async def list_configs(self, user_id: UUID) -> list[LLMConfigRead]:
        # 列表接口不返回 api_key_ciphertext，只返回是否已配置 API Key。
        configs = await self.configs.list_active(user_id)
        return [self._to_read(config) for config in configs]

    async def list_providers(self) -> list[LLMProviderRead]:
        # Provider 元信息来自 registry，不从数据库读取，方便前端生成配置表单。
        return [
            LLMProviderRead(
                id=provider.id,
                name=provider.name,
                requires_api_key=provider.requires_api_key,
                supports_custom_base_url=provider.supports_custom_base_url,
                supports_model_list=provider.supports_model_list,
                default_base_url=provider.default_base_url,
            )
            for provider in list_provider_infos()
        ]

    async def list_config_models(self, *, user_id: UUID, config_id: UUID) -> list[LLMModelRead]:
        # 已保存配置走这里：先做用户归属校验，再解密 API Key 交给 provider。
        config = await self._get_owned_config(user_id=user_id, config_id=config_id)
        provider = get_provider(config.provider)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="暂不支持的模型供应商")

        try:
            # provider 层负责不同供应商的 SDK 差异，service 只负责错误语义转换。
            models = await provider.list_models(provider.from_model_config(config))
        except LLMProviderError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"获取模型列表失败：{exc}") from exc
        return [self._to_model_read(model) for model in models]

    async def probe_models(self, payload: LLMModelProbe) -> list[LLMModelRead]:
        # 未保存配置走这里：请求中的 api_key 只用于本次探测，不进入数据库。
        provider = get_provider(payload.provider)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="暂不支持的模型供应商")

        runtime_config = LLMRuntimeConfig(
            provider=payload.provider,
            model=None,
            base_url=payload.base_url,
            api_key=payload.api_key,
            provider_options=payload.provider_options,
        )
        try:
            # runtime_config 是临时配置对象，结构对齐 LLMConfig 但不包含用户归属。
            models = await provider.list_models(runtime_config)
        except LLMProviderError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"获取模型列表失败：{exc}") from exc
        return [self._to_model_read(model) for model in models]

    async def get_config(self, *, user_id: UUID, config_id: UUID) -> LLMConfigRead:
        config = await self._get_owned_config(user_id=user_id, config_id=config_id)
        return self._to_read(config)

    async def create_config(self, *, user_id: UUID, payload: LLMConfigCreate) -> LLMConfigRead:
        # 同一用户下活跃配置名称保持唯一，方便前端做展示和切换。
        await self._ensure_name_available(user_id=user_id, name=payload.name)

        # 第一条模型配置自动成为默认配置，保证创建会话时有可用兜底。
        is_first_config = await self.configs.count_active(user_id) == 0
        is_default = payload.is_default or is_first_config
        if is_default:
            await self.configs.unset_defaults(user_id)
        # 入库前把 claude/google 等别名规范化，历史消息上的 provider 快照更稳定。
        provider = self._normalize_provider_or_400(payload.provider)

        models = self._clean_models_or_400(payload.models)

        # API Key 在进入数据库前加密；后续响应永远不返回明文或密文。
        config = await self.configs.create(
            user_id=user_id,
            name=payload.name,
            provider=provider,
            models=models,
            base_url=payload.base_url,
            api_key_ciphertext=encrypt_secret(payload.api_key),
            provider_options=payload.provider_options,
            is_default=is_default,
            supports_vision=payload.supports_vision,
        )
        await self.session.commit()
        return self._to_read(config)

    async def update_config(
        self,
        *,
        user_id: UUID,
        config_id: UUID,
        payload: LLMConfigUpdate,
    ) -> LLMConfigRead:
        config = await self._get_owned_config(user_id=user_id, config_id=config_id)
        update_data = payload.model_dump(exclude_unset=True)

        # PATCH 只更新显式传入的字段，避免把未传字段误清空。
        if "name" in update_data and update_data["name"] != config.name:
            await self._ensure_name_available(user_id=user_id, name=update_data["name"])
            config.name = update_data["name"]
        if "provider" in update_data:
            config.provider = self._normalize_provider_or_400(update_data["provider"])
        if "models" in update_data:
            config.models = self._clean_models_or_400(update_data["models"])
        if "base_url" in update_data:
            config.base_url = update_data["base_url"]
        if "api_key" in update_data:
            # 允许传空字符串清除 API Key；非空值会重新加密后保存。
            config.api_key_ciphertext = encrypt_secret(update_data["api_key"])
        if "provider_options" in update_data:
            config.provider_options = update_data["provider_options"] or {}
        if "is_enabled" in update_data:
            config.is_enabled = update_data["is_enabled"]
        if update_data.get("is_default") is True:
            # 数据库有“每用户一个默认配置”的部分唯一索引，这里先清空旧默认。
            await self.configs.unset_defaults(user_id)
            config.is_default = True
        elif update_data.get("is_default") is False:
            config.is_default = False
        if "supports_vision" in update_data:
            config.supports_vision = update_data["supports_vision"]

        await self.session.commit()
        await self.session.refresh(config)
        return self._to_read(config)

    async def set_default(self, *, user_id: UUID, config_id: UUID) -> LLMConfigRead:
        config = await self._get_owned_config(user_id=user_id, config_id=config_id)
        # 已停用配置不能作为新会话或新模型调用的默认配置。
        if not config.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不能将已停用的模型配置设为默认",
            )

        await self.configs.unset_defaults(user_id)
        config.is_default = True
        await self.session.commit()
        await self.session.refresh(config)
        return self._to_read(config)

    async def archive_config(self, *, user_id: UUID, config_id: UUID) -> None:
        # 删除采用软删除，保留历史消息上的模型快照和外键可追溯性。
        config = await self._get_owned_config(user_id=user_id, config_id=config_id)
        await self.configs.archive(config)
        await self.session.commit()

    async def _get_owned_config(self, *, user_id: UUID, config_id: UUID) -> LLMConfig:
        # 所有配置读取都带 user_id，避免跨用户访问其他人的模型配置。
        config = await self.configs.get_active(user_id=user_id, config_id=config_id)
        if config is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")
        return config

    async def _ensure_name_available(self, *, user_id: UUID, name: str) -> None:
        existing = await self.configs.get_active_by_name(user_id=user_id, name=name)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="模型配置名称已存在",
            )

    def _to_read(self, config: LLMConfig) -> LLMConfigRead:
        # Pydantic 从 ORM 对象取公共字段，再手动补 has_api_key 这个派生字段。
        data = LLMConfigRead.model_validate(config)
        return data.model_copy(update={"has_api_key": bool(config.api_key_ciphertext)})

    def _normalize_provider_or_400(self, provider_id: str) -> str:
        # 所有配置写入都经过 registry，避免后续聊天时才发现 provider 不支持。
        provider = get_provider(provider_id)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="暂不支持的模型供应商")
        return provider.provider

    def _clean_models_or_400(self, models: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(model.strip() for model in models if model.strip()))
        if not cleaned:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要配置一个模型")
        return cleaned

    def _to_model_read(self, model) -> LLMModelRead:
        # Provider 内部统一返回 dataclass，这里再转换成 API schema。
        return LLMModelRead(
            id=model.id,
            name=model.name,
            description=model.description,
            owned_by=model.owned_by,
        )

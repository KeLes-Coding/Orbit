from __future__ import annotations

import re
from dataclasses import replace

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.models.llm_config import LLMConfig
from app.services.llm.providers.base import LLMProviderError, LLMRuntimeConfig
from app.services.llm.providers.registry import get_provider


class ConversationTitleGenerator:
    # 生成会话标题的小模型能力；失败时必须退化为本地截断，不能阻塞首条消息。
    max_attempts = 2

    async def generate(self, *, user_message: str, fallback_config: LLMConfig | None) -> str:
        # 优先使用专门的标题模型；未配置时再尝试复用当前会话模型。
        runtime_config = self._build_runtime_config(fallback_config)
        if runtime_config is None:
            return self.fallback_title(user_message)

        # 标题生成属于首条消息链路里的辅助能力，连续失败后直接本地降级。
        for _ in range(self.max_attempts):
            try:
                title = await self._generate_once(user_message=user_message, config=runtime_config)
            except Exception:
                continue
            normalized = self.normalize_title(title, user_message=user_message)
            if normalized:
                return normalized

        return self.fallback_title(user_message)

    async def _generate_once(self, *, user_message: str, config: LLMRuntimeConfig) -> str:
        provider = get_provider(config.provider)
        if provider is None:
            raise LLMProviderError(f"暂不支持的标题模型供应商：{config.provider}")

        # prompt 强约束“只返回标题”，降低后续清洗成本。
        chat_model = provider.build_chat_model(config)
        response = await chat_model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是对话标题生成器。根据用户第一条消息生成一个简短标题。"
                        "只输出标题本身，不要解释、编号、引号或标点包装。"
                        f"标题最长 {settings.title_max_chars} 个字符。"
                    )
                ),
                HumanMessage(content=user_message),
            ]
        )
        return str(getattr(response, "content", "") or "")

    def _build_runtime_config(self, fallback_config: LLMConfig | None) -> LLMRuntimeConfig | None:
        # ORBIT_TITLE_* 存在时使用独立小模型，避免标题任务影响用户选择的聊天模型。
        if settings.title_provider and settings.title_model:
            return LLMRuntimeConfig(
                provider=settings.title_provider,
                model=settings.title_model,
                base_url=settings.title_base_url,
                api_key=settings.title_api_key,
                provider_options={
                    "connection": {"timeout": settings.title_timeout_seconds},
                    "generation": {"temperature": 0},
                },
            )

        if fallback_config is None:
            return None

        provider = get_provider(fallback_config.provider)
        if provider is None:
            return None

        # 复用会话模型时只收紧 temperature 和 timeout，其余配置沿用用户设置。
        runtime_config = provider.from_model_config(fallback_config)
        options = dict(runtime_config.provider_options or {})
        generation = dict(options.get("generation") or {})
        generation.setdefault("temperature", 0)
        connection = dict(options.get("connection") or {})
        connection.setdefault("timeout", settings.title_timeout_seconds)
        options["generation"] = generation
        options["connection"] = connection
        return replace(runtime_config, provider_options=options)

    def normalize_title(self, raw_title: str, *, user_message: str) -> str:
        # 模型偶尔会带上“标题：”或引号，这里把展示层不需要的包装去掉。
        title = self._clean_text(raw_title)
        prefixes = (
            "title:",
            "标题:",
            "标题：",
            "conversation title:",
            "chat title:",
        )
        lowered = title.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                title = title[len(prefix) :].strip()
                break

        title = title.strip(" \t\r\n\"'`“”‘’《》")
        title = self._clean_text(title)
        if not title:
            return ""
        # 如果模型只是原样复读用户输入，改用统一的本地截断策略。
        if title == self._clean_text(user_message):
            return self.fallback_title(user_message)
        return self.truncate_title(title)

    def fallback_title(self, user_message: str) -> str:
        # 兜底标题必须纯本地可用，保证无模型/模型失败时也能创建会话。
        cleaned = self._clean_text(user_message)
        if not cleaned:
            return "Untitled chat"
        return self.truncate_title(cleaned)

    def truncate_title(self, title: str) -> str:
        max_chars = max(8, settings.title_max_chars)
        if len(title) <= max_chars:
            return title
        return f"{title[: max_chars - 3].rstrip()}..."

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

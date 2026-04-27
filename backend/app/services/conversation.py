from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.llm_config import LLMConfigRepository
from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    MessageExchangeRead,
    MessageRead,
)
from app.services.llm_client import LLMClient, LLMClientError


class ConversationService:
    # 会话服务负责会话归属校验、默认模型选择和消息顺序写入。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.llm_configs = LLMConfigRepository(session)
        self.llm_client = LLMClient()

    async def list_conversations(self, user_id: UUID) -> list[ConversationRead]:
        # 会话列表只返回未归档会话，并按 updated_at 倒序排列。
        conversations = await self.conversations.list_active(user_id)
        return [ConversationRead.model_validate(conversation) for conversation in conversations]

    async def get_conversation(self, *, user_id: UUID, conversation_id: UUID) -> ConversationRead:
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return ConversationRead.model_validate(conversation)

    async def create_conversation(
        self,
        *,
        user_id: UUID,
        payload: ConversationCreate,
    ) -> ConversationRead:
        # 创建会话时如果前端没指定模型配置，就尝试使用当前用户的默认配置。
        llm_config_id = payload.llm_config_id
        if llm_config_id is None:
            llm_config_id = await self._get_default_llm_config_id(user_id)
        elif await self.llm_configs.get_active(user_id=user_id, config_id=llm_config_id) is None:
            # 指定的模型配置必须属于当前用户，不能跨用户引用。
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")

        conversation = await self.conversations.create(
            user_id=user_id,
            title=payload.title,
            llm_config_id=llm_config_id,
            chat_mode=payload.chat_mode,
            metadata=payload.metadata,
        )
        await self.session.commit()
        return ConversationRead.model_validate(conversation)

    async def update_conversation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        payload: ConversationUpdate,
    ) -> ConversationRead:
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        update_data = payload.model_dump(exclude_unset=True)

        # PATCH 只更新显式传入的会话字段。
        if "title" in update_data:
            conversation.title = update_data["title"]
        if "llm_config_id" in update_data:
            llm_config_id = update_data["llm_config_id"]
            if llm_config_id is not None and await self.llm_configs.get_active(
                user_id=user_id,
                config_id=llm_config_id,
            ) is None:
                # 切换会话默认模型时，也必须确认配置属于当前用户。
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")
            conversation.llm_config_id = llm_config_id
        if "metadata" in update_data:
            conversation.metadata_ = update_data["metadata"] or {}

        await self.session.commit()
        await self.session.refresh(conversation)
        return ConversationRead.model_validate(conversation)

    async def archive_conversation(self, *, user_id: UUID, conversation_id: UUID) -> None:
        # 会话删除同样使用软删除，消息历史仍保留在数据库中。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        await self.conversations.archive(conversation)
        await self.session.commit()

    async def list_messages(self, *, user_id: UUID, conversation_id: UUID) -> list[MessageRead]:
        # 先校验会话归属，再读取消息，避免用户枚举 conversation_id 读取他人历史。
        await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        messages = await self.messages.list_by_conversation(conversation_id)
        return [MessageRead.model_validate(message) for message in messages]

    async def create_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> MessageExchangeRead:
        # 本接口完成“一问一答”：写入用户消息、创建 assistant 占位、调用模型并落库结果。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        llm_config_id = conversation.llm_config_id or await self._get_default_llm_config_id(user_id)
        if llm_config_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请先创建并启用一个默认模型配置",
            )

        llm_config = await self.llm_configs.get_active(user_id=user_id, config_id=llm_config_id)
        if llm_config is None or not llm_config.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前会话的模型配置不可用",
            )

        user_message = await self.messages.create_user_message(
            conversation_id=conversation_id,
            content=content,
        )
        history_messages = await self.messages.list_by_conversation(conversation_id)
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation_id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=llm_config.model,
        )
        # 先提交占位消息，长时间模型调用时数据库里也能看到 streaming 状态。
        await self.conversations.touch(conversation_id)
        await self.session.commit()

        try:
            completion = await self.llm_client.generate(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
            )
        except LLMClientError as exc:
            assistant_message = await self.messages.fail_assistant_message(
                message=assistant_message,
                error=str(exc),
            )
            await self.conversations.touch(conversation_id)
            await self.session.commit()
            return MessageExchangeRead(
                user_message=MessageRead.model_validate(user_message),
                assistant_message=MessageRead.model_validate(assistant_message),
            )

        assistant_message = await self.messages.complete_assistant_message(
            message=assistant_message,
            content=completion.content,
            token_usage=completion.token_usage,
            response_metadata=completion.response_metadata,
        )
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return MessageExchangeRead(
            user_message=MessageRead.model_validate(user_message),
            assistant_message=MessageRead.model_validate(assistant_message),
        )

    async def _get_owned_conversation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
    ) -> Conversation:
        # 所有会话操作都必须带 user_id，保证多用户数据隔离。
        conversation = await self.conversations.get_active(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        return conversation

    async def _get_default_llm_config_id(self, user_id: UUID) -> UUID | None:
        # 没有默认配置时允许创建无模型会话，后续发送模型请求前再做强校验。
        configs = await self.llm_configs.list_active(user_id)
        for config in configs:
            if config.is_default and config.is_enabled:
                return config.id
        return None

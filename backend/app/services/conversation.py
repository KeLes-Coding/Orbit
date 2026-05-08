import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.llm_config import LLMConfigRepository
from app.schemas.conversation import (
    BranchSwitchRead,
    ConversationCreate,
    ConversationForkCreate,
    ConversationForkRead,
    ConversationMessageCreate,
    ConversationRead,
    ConversationUpdate,
    MessageEdit,
    MessageExchangeRead,
    MessageRead,
)
from app.services.generation.title import ConversationTitleGenerator
from app.services.llm_client import LLMClient, LLMClientError
from app.services.streaming import message_stream_registry


@dataclass(frozen=True)
class ConversationStreamEvent:
    event: str
    data: dict[str, Any]


class ConversationService:
    # 会话服务负责会话归属校验、默认模型选择和消息顺序写入。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.llm_configs = LLMConfigRepository(session)
        self.llm_client = LLMClient()
        self.title_generator = ConversationTitleGenerator()

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
        conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        messages = await self.messages.list_visible_path(conversation)
        return await self._message_reads(messages)

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
            for_update=True,
        )
        llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)

        parent_message = await self._get_active_leaf(conversation)
        user_message = await self.messages.create_user_message(
            conversation_id=conversation_id,
            content=content,
            parent_message=parent_message,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
        history_messages = await self.messages.list_path_to_message(
            conversation_id=conversation_id,
            message_id=user_message.id,
        )
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation_id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=llm_config.model,
            parent_message=user_message,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
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
                user_message=await self._message_read(user_message),
                assistant_message=await self._message_read(assistant_message),
            )

        assistant_message = await self.messages.complete_assistant_message(
            message=assistant_message,
            content=completion.content,
            reasoning_content=completion.reasoning_content,
            token_usage=completion.token_usage,
            response_metadata=completion.response_metadata,
        )
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return MessageExchangeRead(
            user_message=await self._message_read(user_message),
            assistant_message=await self._message_read(assistant_message),
        )

    async def regenerate_assistant(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> MessageRead:
        # Regenerate 不覆盖旧 assistant，而是在同一个 user parent 下创建 assistant sibling。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None or target.role != "assistant" or target.parent_message_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        parent = await self.messages.get_by_id(
            conversation_id=conversation_id,
            message_id=target.parent_message_id,
        )
        if parent is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到重发上下文")

        llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
        history_messages = await self.messages.list_path_to_message(
            conversation_id=conversation_id,
            message_id=parent.id,
        )
        # 新 assistant 创建时会把 parent.active_child 切到新版本。
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation_id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=llm_config.model,
            parent_message=parent,
            source_message_id=target.id,
            revision_type="regenerate",
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
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
            return await self._message_read(assistant_message)

        assistant_message = await self.messages.complete_assistant_message(
            message=assistant_message,
            content=completion.content,
            reasoning_content=completion.reasoning_content,
            token_usage=completion.token_usage,
            response_metadata=completion.response_metadata,
        )
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return await self._message_read(assistant_message)

    async def edit_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: MessageEdit,
    ) -> MessageExchangeRead:
        # Edit 不修改旧 user，而是在原 parent 下创建新的 user sibling。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None or target.role != "user":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")

        parent = None
        if target.parent_message_id is not None:
            parent = await self.messages.get_by_id(
                conversation_id=conversation_id,
                message_id=target.parent_message_id,
            )
            if parent is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到编辑上下文")

        llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
        user_message = await self.messages.create_user_message(
            conversation_id=conversation_id,
            content=payload.content,
            parent_message=parent,
            source_message_id=target.id,
            revision_type="edit",
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
        history_messages = await self.messages.list_path_to_message(
            conversation_id=conversation_id,
            message_id=user_message.id,
        )
        # 编辑后的 user 会继续生成一个新的 assistant child，形成新的 visible path。
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation_id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=llm_config.model,
            parent_message=user_message,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
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
                user_message=await self._message_read(user_message),
                assistant_message=await self._message_read(assistant_message),
            )

        assistant_message = await self.messages.complete_assistant_message(
            message=assistant_message,
            content=completion.content,
            reasoning_content=completion.reasoning_content,
            token_usage=completion.token_usage,
            response_metadata=completion.response_metadata,
        )
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return MessageExchangeRead(
            user_message=await self._message_read(user_message),
            assistant_message=await self._message_read(assistant_message),
        )

    async def stream_regenerate_assistant(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        should_cancel: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # Regenerate 的产品语义是重发 assistant，因此也必须走流式生成。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None or target.role != "assistant" or target.parent_message_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        parent = await self.messages.get_by_id(
            conversation_id=conversation_id,
            message_id=target.parent_message_id,
        )
        if parent is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到重发上下文")

        llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
        history_messages = await self.messages.list_path_to_message(
            conversation_id=conversation_id,
            message_id=parent.id,
        )
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation_id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=llm_config.model,
            parent_message=parent,
            source_message_id=target.id,
            revision_type="regenerate",
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
        await self.conversations.touch(conversation_id)
        await self.session.commit()

        created_data = {
            "assistant_message": (await self._message_read(assistant_message)).model_dump(mode="json")
        }
        async for event in self._stream_assistant_completion(
            conversation_id=conversation_id,
            assistant_message=assistant_message,
            llm_config=llm_config,
            history_messages=history_messages,
            summary=conversation.summary,
            created_data=created_data,
            should_cancel=should_cancel,
        ):
            yield event

    async def stream_edit_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: MessageEdit,
        should_cancel: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # Edit 创建新的 user sibling 后，也立刻流式生成其 assistant child。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None or target.role != "user":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")

        parent = None
        if target.parent_message_id is not None:
            parent = await self.messages.get_by_id(
                conversation_id=conversation_id,
                message_id=target.parent_message_id,
            )
            if parent is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到编辑上下文")

        llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
        user_message = await self.messages.create_user_message(
            conversation_id=conversation_id,
            content=payload.content,
            parent_message=parent,
            source_message_id=target.id,
            revision_type="edit",
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
        history_messages = await self.messages.list_path_to_message(
            conversation_id=conversation_id,
            message_id=user_message.id,
        )
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation_id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=llm_config.model,
            parent_message=user_message,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
        await self.conversations.touch(conversation_id)
        await self.session.commit()

        created_data = {
            "user_message": (await self._message_read(user_message)).model_dump(mode="json"),
            "assistant_message": (await self._message_read(assistant_message)).model_dump(mode="json"),
        }
        async for event in self._stream_assistant_completion(
            conversation_id=conversation_id,
            assistant_message=assistant_message,
            llm_config=llm_config,
            history_messages=history_messages,
            summary=conversation.summary,
            created_data=created_data,
            should_cancel=should_cancel,
        ):
            yield event

    async def switch_branch(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> BranchSwitchRead:
        # 切换 branch 只改局部分叉点的 active_child，再沿 active_child 链恢复子路径。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        if target.parent_message_id is not None:
            parent = await self.messages.get_by_id(
                conversation_id=conversation_id,
                message_id=target.parent_message_id,
            )
            if parent is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到分支父节点")
            # message_id 是目标 sibling；其 parent 需要把 active_child 指向它。
            parent.active_child_message_id = target.id

        # 不找最深 leaf，而是沿目标 sibling 自己的 active_child 选择向下恢复。
        leaf = await self.messages.resolve_active_leaf_from(
            conversation_id=conversation_id,
            message=target,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=leaf)
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        messages = await self.messages.list_visible_path(conversation)
        return BranchSwitchRead(
            active_leaf_message_id=conversation.active_leaf_message_id,
            messages=await self._message_reads(messages),
        )

    async def fork_conversation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: ConversationForkCreate,
    ) -> ConversationForkRead:
        # Fork v1 只允许从当前 visible path 上的节点复制，避免复制未选中的 sibling 子树。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        visible_path = await self.messages.list_visible_path(conversation)
        target_index = next((index for index, message in enumerate(visible_path) if message.id == message_id), None)
        if target_index is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="只能从当前可见路径 fork")

        # 新会话独立拥有 thread、summary 和 active path，只保留来源字段用于追溯。
        new_conversation = await self.conversations.create(
            user_id=user_id,
            title=payload.title or conversation.title,
            llm_config_id=conversation.llm_config_id,
            chat_mode=conversation.chat_mode,
            metadata=dict(conversation.metadata_ or {}),
            forked_from_conversation_id=conversation.id,
            forked_from_message_id=message_id,
        )
        copied_messages = []
        parent_copy = None
        for source_message in visible_path[: target_index + 1]:
            # 按 root -> target 顺序复制，逐条重建 parent 和 active_child。
            copied = await self.messages.clone_message_to_conversation(
                source=source_message,
                target_conversation_id=new_conversation.id,
                parent_message=parent_copy,
            )
            copied_messages.append(copied)
            parent_copy = copied

        await self.messages.set_conversation_active_leaf(conversation=new_conversation, message=parent_copy)
        await self.conversations.touch(new_conversation.id)
        await self.session.commit()
        await self.session.refresh(new_conversation)
        return ConversationForkRead(
            conversation=ConversationRead.model_validate(new_conversation),
            messages=await self._message_reads(copied_messages),
        )

    async def stream_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
        should_cancel: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # 流式接口沿用一问一答写入顺序，只是 assistant 内容通过 SSE 增量返回。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)

        parent_message = await self._get_active_leaf(conversation)
        user_message = await self.messages.create_user_message(
            conversation_id=conversation_id,
            content=content,
            parent_message=parent_message,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
        history_messages = await self.messages.list_path_to_message(
            conversation_id=conversation_id,
            message_id=user_message.id,
        )
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation_id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=llm_config.model,
            parent_message=user_message,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
        # 占位消息先提交，让前端拿到真实 message_id，也方便取消接口定位运行中的回复。
        await self.conversations.touch(conversation_id)
        await self.session.commit()

        active_stream = await message_stream_registry.register(assistant_message.id)
        full_content_parts: list[str] = []
        full_reasoning_parts: list[str] = []
        token_usage: dict[str, Any] = {}
        response_metadata: dict[str, Any] = {"provider": llm_config.provider, "model": llm_config.model}
        finish_reason: str | None = None

        try:
            yield ConversationStreamEvent(
                event="message.created",
                data={
                    "user_message": (await self._message_read(user_message)).model_dump(mode="json"),
                    "assistant_message": (await self._message_read(assistant_message)).model_dump(mode="json"),
                },
            )
            # 预取首事件发生在路由任务里，后续模型流在 StreamingResponse 任务里执行。
            await message_stream_registry.attach_current_task(assistant_message.id)
            if await self._stream_should_cancel(active_stream.cancel_event, should_cancel):
                cancelled_message = await self._cancel_streaming_message(
                    conversation_id=conversation_id,
                    assistant_message=assistant_message,
                    content="",
                    token_usage=token_usage,
                    response_metadata=response_metadata,
                )
                yield ConversationStreamEvent(
                    event="message.cancelled",
                    data={
                            "message": (await self._message_read(cancelled_message)).model_dump(mode="json")
                    },
                )
                return

            async for chunk in self.llm_client.stream(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
            ):
                # 手动取消和浏览器断开都在这里收敛成同一种停止语义。
                if await self._stream_should_cancel(active_stream.cancel_event, should_cancel):
                    cancelled_message = await self._cancel_streaming_message(
                        conversation_id=conversation_id,
                        assistant_message=assistant_message,
                        content="".join(full_content_parts),
                        reasoning_content="".join(full_reasoning_parts),
                        token_usage=token_usage,
                        response_metadata=response_metadata,
                    )
                    yield ConversationStreamEvent(
                        event="message.cancelled",
                        data={"message": (await self._message_read(cancelled_message)).model_dump(mode="json")},
                    )
                    return

                if chunk.token_usage:
                    token_usage = chunk.token_usage
                if chunk.response_metadata:
                    response_metadata.update(chunk.response_metadata)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

                if chunk.reasoning_delta:
                    full_reasoning_parts.append(chunk.reasoning_delta)
                    yield ConversationStreamEvent(
                        event="message.reasoning_delta",
                        data={
                            "message_id": str(assistant_message.id),
                            "delta": chunk.reasoning_delta,
                        },
                    )

                if chunk.content_delta:
                    full_content_parts.append(chunk.content_delta)
                    yield ConversationStreamEvent(
                        event="message.delta",
                        data={
                            "message_id": str(assistant_message.id),
                            "delta": chunk.content_delta,
                        },
                    )

            full_content = "".join(full_content_parts)
            full_reasoning = "".join(full_reasoning_parts)
            if not full_content:
                raise LLMClientError("模型服务没有返回 assistant 内容")

            if finish_reason:
                response_metadata["finish_reason"] = finish_reason
            assistant_message = await self.messages.complete_assistant_message(
                message=assistant_message,
                content=full_content,
                reasoning_content=full_reasoning,
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            await self.conversations.touch(conversation_id)
            await self.session.commit()
            yield ConversationStreamEvent(
                event="message.completed",
                data={"message": (await self._message_read(assistant_message)).model_dump(mode="json")},
            )
        except asyncio.CancelledError:
            # cancel endpoint 会取消当前任务；这里负责把已生成内容保存为 cancelled。
            await self._cancel_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            return
        except LLMClientError as exc:
            failed_message = await self._fail_or_partial_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                error=str(exc),
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            yield ConversationStreamEvent(
                event="message.failed",
                data={"message": (await self._message_read(failed_message)).model_dump(mode="json")},
            )
        finally:
            await message_stream_registry.unregister(assistant_message.id)

    async def stream_new_conversation_message(
        self,
        *,
        user_id: UUID,
        payload: ConversationMessageCreate,
        should_cancel: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # New Chat 的首条消息会从这里进入：先确定可用模型，再创建会话与消息。
        llm_config_id = payload.llm_config_id or await self._get_default_llm_config_id(user_id)
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

        # 标题生成失败会在 generator 内部降级为用户消息截断，不影响后续发起对话。
        title = await self.title_generator.generate(
            user_message=payload.content,
            fallback_config=llm_config,
        )
        # 此时才落库 conversation，避免用户只是点击 New Chat 就产生空会话。
        conversation = await self.conversations.create(
            user_id=user_id,
            title=title,
            llm_config_id=llm_config.id,
            chat_mode=payload.chat_mode,
            metadata=payload.metadata,
        )
        user_message = await self.messages.create_user_message(
            conversation_id=conversation.id,
            content=payload.content,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
        history_messages = await self.messages.list_path_to_message(
            conversation_id=conversation.id,
            message_id=user_message.id,
        )
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation.id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=llm_config.model,
            parent_message=user_message,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
        await self.conversations.touch(conversation.id)
        await self.session.commit()
        await self.session.refresh(conversation)

        # 注册流式任务，取消接口可通过 assistant_message.id 找到当前生成。
        active_stream = await message_stream_registry.register(assistant_message.id)
        full_content_parts: list[str] = []
        full_reasoning_parts: list[str] = []
        token_usage: dict[str, Any] = {}
        response_metadata: dict[str, Any] = {"provider": llm_config.provider, "model": llm_config.model}
        finish_reason: str | None = None

        try:
            # 先把真实会话发给前端，用生成后的标题替换本地 pending 项。
            yield ConversationStreamEvent(
                event="conversation.created",
                data={
                    "conversation": ConversationRead.model_validate(conversation).model_dump(
                        mode="json"
                    )
                },
            )
            # 再发送本轮真实 user/assistant 消息，替换前端本地占位。
            yield ConversationStreamEvent(
                event="message.created",
                data={
                    "user_message": (await self._message_read(user_message)).model_dump(mode="json"),
                    "assistant_message": (await self._message_read(assistant_message)).model_dump(mode="json"),
                },
            )
            await message_stream_registry.attach_current_task(assistant_message.id)
            if await self._stream_should_cancel(active_stream.cancel_event, should_cancel):
                cancelled_message = await self._cancel_streaming_message(
                    conversation_id=conversation.id,
                    assistant_message=assistant_message,
                    content="",
                    token_usage=token_usage,
                    response_metadata=response_metadata,
                )
                yield ConversationStreamEvent(
                    event="message.cancelled",
                    data={
                            "message": (await self._message_read(cancelled_message)).model_dump(mode="json")
                    },
                )
                return

            async for chunk in self.llm_client.stream(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
            ):
                # 用户点击停止或浏览器断开时，保留已生成内容并落成 cancelled。
                if await self._stream_should_cancel(active_stream.cancel_event, should_cancel):
                    cancelled_message = await self._cancel_streaming_message(
                        conversation_id=conversation.id,
                        assistant_message=assistant_message,
                        content="".join(full_content_parts),
                        reasoning_content="".join(full_reasoning_parts),
                        token_usage=token_usage,
                        response_metadata=response_metadata,
                    )
                    yield ConversationStreamEvent(
                        event="message.cancelled",
                        data={"message": (await self._message_read(cancelled_message)).model_dump(mode="json")},
                    )
                    return

                if chunk.token_usage:
                    token_usage = chunk.token_usage
                if chunk.response_metadata:
                    response_metadata.update(chunk.response_metadata)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

                if chunk.reasoning_delta:
                    # reasoning 使用独立 SSE 事件，前端可以单独渲染 thinking 块而不污染正文。
                    full_reasoning_parts.append(chunk.reasoning_delta)
                    yield ConversationStreamEvent(
                        event="message.reasoning_delta",
                        data={
                            "message_id": str(assistant_message.id),
                            "delta": chunk.reasoning_delta,
                        },
                    )

                if chunk.content_delta:
                    # SSE 正文增量仍保持旧事件名；最终完整内容以后端落库消息为准。
                    full_content_parts.append(chunk.content_delta)
                    yield ConversationStreamEvent(
                        event="message.delta",
                        data={
                            "message_id": str(assistant_message.id),
                            "delta": chunk.content_delta,
                        },
                    )

            full_content = "".join(full_content_parts)
            full_reasoning = "".join(full_reasoning_parts)
            if not full_content:
                raise LLMClientError("模型服务没有返回 assistant 内容")

            # 流式完成后把完整 assistant 内容和用量统一写回数据库。
            if finish_reason:
                response_metadata["finish_reason"] = finish_reason
            assistant_message = await self.messages.complete_assistant_message(
                message=assistant_message,
                content=full_content,
                reasoning_content=full_reasoning,
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            await self.conversations.touch(conversation.id)
            await self.session.commit()
            yield ConversationStreamEvent(
                event="message.completed",
                data={"message": (await self._message_read(assistant_message)).model_dump(mode="json")},
            )
        except asyncio.CancelledError:
            # 任务被外部取消时也要把已有增量保存下来，避免遗留 streaming 状态。
            await self._cancel_streaming_message(
                conversation_id=conversation.id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            return
        except LLMClientError as exc:
            # 模型失败但已有部分内容时会保留 partial；完全无内容则标记 failed。
            failed_message = await self._fail_or_partial_streaming_message(
                conversation_id=conversation.id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                error=str(exc),
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            yield ConversationStreamEvent(
                event="message.failed",
                data={"message": (await self._message_read(failed_message)).model_dump(mode="json")},
            )
        finally:
            await message_stream_registry.unregister(assistant_message.id)

    async def cancel_message_generation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> MessageRead:
        await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        message = await self.messages.get_by_id(
            conversation_id=conversation_id,
            message_id=message_id,
        )
        if message is None or message.role != "assistant":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        if message.status != "streaming":
            return await self._message_read(message)

        # 如果本进程内存在活跃流，只发停止信号，由流式协程负责最终落库。
        did_signal = await message_stream_registry.cancel(message_id)
        if did_signal:
            return await self._message_read(message)

        # 找不到活跃流时兜底更新数据库，避免遗留 streaming 状态。
        message = await self.messages.cancel_assistant_message(
            message=message,
            content=message.content,
            reasoning_content=message.reasoning_content,
            token_usage=message.token_usage,
            response_metadata=message.response_metadata,
        )
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return await self._message_read(message)

    async def _get_owned_conversation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        for_update: bool = False,
    ) -> Conversation:
        # 所有会话操作都必须带 user_id，保证多用户数据隔离。
        conversation = await self.conversations.get_active(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=for_update,
        )
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        return conversation

    async def _get_active_leaf(self, conversation: Conversation):
        if conversation.active_leaf_message_id is None:
            return None
        message = await self.messages.get_by_id(
            conversation_id=conversation.id,
            message_id=conversation.active_leaf_message_id,
        )
        if message is None:
            return None
        return message

    async def _get_usable_llm_config(self, *, user_id: UUID, conversation: Conversation):
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
        return llm_config

    async def _message_read(self, message) -> MessageRead:
        state = await self.messages.get_message_read_state(message)
        return MessageRead.model_validate(message).model_copy(update=state)

    async def _message_reads(self, messages) -> list[MessageRead]:
        return [await self._message_read(message) for message in messages]

    async def _get_default_llm_config_id(self, user_id: UUID) -> UUID | None:
        # 没有默认配置时允许创建无模型会话，后续发送模型请求前再做强校验。
        configs = await self.llm_configs.list_active(user_id)
        for config in configs:
            if config.is_default and config.is_enabled:
                return config.id
        return None

    async def _stream_should_cancel(
        self,
        cancel_event: asyncio.Event,
        should_cancel: Callable[[], Awaitable[bool]] | None,
    ) -> bool:
        # should_cancel 来自 request.is_disconnected，用于处理前端直接关闭连接。
        if cancel_event.is_set():
            return True
        if should_cancel is not None and await should_cancel():
            return True
        return False

    async def _stream_assistant_completion(
        self,
        *,
        conversation_id: UUID,
        assistant_message,
        llm_config,
        history_messages,
        summary: str | None,
        created_data: dict[str, Any],
        should_cancel: Callable[[], Awaitable[bool]] | None,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # 统一处理 branch 场景下 assistant 的流式生成、取消和失败落库。
        active_stream = await message_stream_registry.register(assistant_message.id)
        full_content_parts: list[str] = []
        full_reasoning_parts: list[str] = []
        token_usage: dict[str, Any] = {}
        response_metadata: dict[str, Any] = {"provider": llm_config.provider, "model": llm_config.model}
        finish_reason: str | None = None

        try:
            yield ConversationStreamEvent(event="message.created", data=created_data)
            await message_stream_registry.attach_current_task(assistant_message.id)
            if await self._stream_should_cancel(active_stream.cancel_event, should_cancel):
                cancelled_message = await self._cancel_streaming_message(
                    conversation_id=conversation_id,
                    assistant_message=assistant_message,
                    content="",
                    token_usage=token_usage,
                    response_metadata=response_metadata,
                )
                yield ConversationStreamEvent(
                    event="message.cancelled",
                    data={"message": (await self._message_read(cancelled_message)).model_dump(mode="json")},
                )
                return

            async for chunk in self.llm_client.stream(
                config=llm_config,
                messages=history_messages,
                summary=summary,
            ):
                if await self._stream_should_cancel(active_stream.cancel_event, should_cancel):
                    cancelled_message = await self._cancel_streaming_message(
                        conversation_id=conversation_id,
                        assistant_message=assistant_message,
                        content="".join(full_content_parts),
                        reasoning_content="".join(full_reasoning_parts),
                        token_usage=token_usage,
                        response_metadata=response_metadata,
                    )
                    yield ConversationStreamEvent(
                        event="message.cancelled",
                        data={"message": (await self._message_read(cancelled_message)).model_dump(mode="json")},
                    )
                    return

                if chunk.token_usage:
                    token_usage = chunk.token_usage
                if chunk.response_metadata:
                    response_metadata.update(chunk.response_metadata)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

                if chunk.reasoning_delta:
                    full_reasoning_parts.append(chunk.reasoning_delta)
                    yield ConversationStreamEvent(
                        event="message.reasoning_delta",
                        data={"message_id": str(assistant_message.id), "delta": chunk.reasoning_delta},
                    )

                if chunk.content_delta:
                    full_content_parts.append(chunk.content_delta)
                    yield ConversationStreamEvent(
                        event="message.delta",
                        data={"message_id": str(assistant_message.id), "delta": chunk.content_delta},
                    )

            full_content = "".join(full_content_parts)
            full_reasoning = "".join(full_reasoning_parts)
            if not full_content:
                raise LLMClientError("模型服务没有返回 assistant 内容")

            if finish_reason:
                response_metadata["finish_reason"] = finish_reason
            assistant_message = await self.messages.complete_assistant_message(
                message=assistant_message,
                content=full_content,
                reasoning_content=full_reasoning,
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            await self.conversations.touch(conversation_id)
            await self.session.commit()
            yield ConversationStreamEvent(
                event="message.completed",
                data={"message": (await self._message_read(assistant_message)).model_dump(mode="json")},
            )
        except asyncio.CancelledError:
            await self._cancel_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            return
        except LLMClientError as exc:
            failed_message = await self._fail_or_partial_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                error=str(exc),
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            yield ConversationStreamEvent(
                event="message.failed",
                data={"message": (await self._message_read(failed_message)).model_dump(mode="json")},
            )
        finally:
            await message_stream_registry.unregister(assistant_message.id)

    async def _cancel_streaming_message(
        self,
        *,
        conversation_id: UUID,
        assistant_message,
        content: str,
        token_usage: dict[str, Any],
        response_metadata: dict[str, Any],
        reasoning_content: str = "",
    ):
        # 取消时不丢弃 assistant 占位消息，而是保存已生成内容并落成最终状态。
        assistant_message = await self.messages.cancel_assistant_message(
            message=assistant_message,
            content=content,
            reasoning_content=reasoning_content,
            token_usage=token_usage,
            response_metadata=response_metadata,
        )
        # 这里提交事务，确保即使 SSE 连接随后关闭，取消状态也已经持久化。
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return assistant_message

    async def _fail_or_partial_streaming_message(
        self,
        *,
        conversation_id: UUID,
        assistant_message,
        content: str,
        error: str,
        token_usage: dict[str, Any],
        response_metadata: dict[str, Any],
        reasoning_content: str = "",
    ):
        # 流式失败时，有正文或 reasoning 就保留 partial，完全没有增量才标记 failed。
        if content or reasoning_content:
            assistant_message = await self.messages.partial_assistant_message(
                message=assistant_message,
                content=content,
                reasoning_content=reasoning_content,
                error=error,
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
        else:
            assistant_message = await self.messages.fail_assistant_message(
                message=assistant_message,
                error=error,
            )
        # 失败/部分失败同样需要落库，前端刷新后才能看到重试或部分结果状态。
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return assistant_message

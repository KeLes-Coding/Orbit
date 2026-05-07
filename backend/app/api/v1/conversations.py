import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.conversation import (
    ActiveStreamRead,
    BranchSwitchRead,
    ConversationCreate,
    ConversationForkCreate,
    ConversationForkRead,
    ConversationMessageCreate,
    ConversationRead,
    ConversationUpdate,
    MessageEdit,
    MessageExchangeRead,
    MessageCreate,
    MessageRegenerate,
    MessageRead,
)
from app.services.conversation import ConversationService, ConversationStreamEvent


router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ConversationRead]:
    # 返回当前用户未归档会话，用于会话侧边栏列表。
    return await ConversationService(session).list_conversations(current_user.id)


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationRead:
    # 新建会话时可以显式选择模型配置，也可以使用默认配置。
    return await ConversationService(session).create_conversation(
        user_id=current_user.id,
        payload=payload,
    )


@router.post("/messages/stream")
async def stream_new_conversation_message(
    payload: ConversationMessageCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    service = ConversationService(session)
    # 首条消息入口先创建 active stream，再把当前请求作为第一个订阅者挂上去。
    conversation_id, stream_id = await service.start_stream_new_conversation_message(
        user_id=current_user.id,
        payload=payload,
    )

    async def event_generator() -> AsyncIterator[str]:
        async for event in service.subscribe_stream(
            user_id=current_user.id,
            conversation_id=conversation_id,
            stream_id=stream_id,
            last_seq=0,
        ):
            yield encode_sse_event(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationRead:
    # 查询会话详情会先校验 user_id 归属。
    return await ConversationService(session).get_conversation(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationRead:
    # 更新标题、默认模型配置或 metadata，历史消息不受影响。
    return await ConversationService(session).update_conversation(
        user_id=current_user.id,
        conversation_id=conversation_id,
        payload=payload,
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_conversation(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    # 会话归档后不再出现在列表中，但数据库仍保留原始消息。
    await ConversationService(session).archive_conversation(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[MessageRead]:
    # 消息读取统一按 sequence_no 升序返回，保证聊天顺序稳定。
    return await ConversationService(session).list_messages(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.post("/{conversation_id}/messages", response_model=MessageExchangeRead, status_code=201)
async def create_user_message(
    conversation_id: UUID,
    payload: MessageCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MessageExchangeRead:
    # 写入用户消息后立即调用模型，并返回本轮 user/assistant 两条消息。
    return await ConversationService(session).create_user_message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        content=payload.content,
        parent_message_id=payload.parent_message_id,
        idempotency_key=payload.idempotency_key,
        model=payload.model,
    )


@router.post("/{conversation_id}/messages/stream")
async def stream_user_message(
    conversation_id: UUID,
    payload: MessageCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    service = ConversationService(session)
    # 发送消息时先启动后台 producer，再把当前连接挂为订阅者。
    stream_id = await service.start_stream_user_message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        content=payload.content,
        parent_message_id=payload.parent_message_id,
        idempotency_key=payload.idempotency_key,
        model=payload.model,
    )

    async def event_generator() -> AsyncIterator[str]:
        async for event in service.subscribe_stream(
            user_id=current_user.id,
            conversation_id=conversation_id,
            stream_id=stream_id,
            last_seq=0,
        ):
            yield encode_sse_event(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/regenerate",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_assistant(
    conversation_id: UUID,
    message_id: UUID,
    *,
    payload: MessageRegenerate | None = None,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MessageRead:
    # 重发 assistant 时，在原 assistant 的 parent 下创建新的 assistant sibling。
    return await ConversationService(session).regenerate_assistant(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        idempotency_key=payload.idempotency_key if payload else None,
        model=payload.model if payload else None,
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/edit",
    response_model=MessageExchangeRead,
    status_code=status.HTTP_201_CREATED,
)
async def edit_user_message(
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageEdit,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MessageExchangeRead:
    # 编辑 user 时创建新的 user sibling，并从新 user 继续生成 assistant。
    return await ConversationService(session).edit_user_message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
        model=payload.model,
    )


@router.post("/{conversation_id}/messages/{message_id}/regenerate/stream")
async def stream_regenerate_assistant(
    conversation_id: UUID,
    message_id: UUID,
    *,
    payload: MessageRegenerate | None = None,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    service = ConversationService(session)
    # 重发 assistant 时同样先创建 active stream，再把当前连接接到 replay/live 订阅上。
    stream_id = await service.start_stream_regenerate_assistant(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        idempotency_key=payload.idempotency_key if payload else None,
        model=payload.model if payload else None,
    )

    async def event_generator() -> AsyncIterator[str]:
        async for event in service.subscribe_stream(
            user_id=current_user.id,
            conversation_id=conversation_id,
            stream_id=stream_id,
            last_seq=0,
        ):
            yield encode_sse_event(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{conversation_id}/messages/{message_id}/edit/stream")
async def stream_edit_user_message(
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageEdit,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    service = ConversationService(session)
    # 编辑 user 后创建新的 user/assistant 分支，并订阅这条新 active stream。
    stream_id = await service.start_stream_edit_user_message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )

    async def event_generator() -> AsyncIterator[str]:
        async for event in service.subscribe_stream(
            user_id=current_user.id,
            conversation_id=conversation_id,
            stream_id=stream_id,
            last_seq=0,
        ):
            yield encode_sse_event(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}/messages/{message_id}/active-stream", response_model=ActiveStreamRead)
async def get_message_active_stream(
    conversation_id: UUID,
    message_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ActiveStreamRead:
    # branch 恢复先查当前 message 所关联的活跃流，再按 stream_id 做 replay/live 订阅。
    return await ConversationService(session).get_message_active_stream(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


@router.get("/{conversation_id}/streams/{stream_id}")
async def subscribe_stream_by_id(
    conversation_id: UUID,
    stream_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    last_seq: int = Query(default=0, ge=0),
) -> StreamingResponse:
    service = ConversationService(session)

    async def event_generator() -> AsyncIterator[str]:
        # 已知 stream_id 时，直接对这条运行实例做 replay + live 订阅。
        async for event in service.subscribe_stream(
            user_id=current_user.id,
            conversation_id=conversation_id,
            stream_id=stream_id,
            last_seq=last_seq,
        ):
            yield encode_sse_event(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}/stream")
async def subscribe_active_stream(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    last_seq: int = Query(default=0, ge=0),
) -> StreamingResponse:
    service = ConversationService(session)

    async def event_generator() -> AsyncIterator[str]:
        # 旧入口暂时保留兼容，后续前端应切到 message -> stream_id 两段式恢复。
        async for event in service.subscribe_active_stream(
            user_id=current_user.id,
            conversation_id=conversation_id,
            last_seq=last_seq,
        ):
            yield encode_sse_event(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/switch",
    response_model=BranchSwitchRead,
)
async def switch_branch(
    conversation_id: UUID,
    message_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BranchSwitchRead:
    # 切换 sibling 后，后端沿 active_child 链恢复该子树上次选择的 visible path。
    return await ConversationService(session).switch_branch(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/fork",
    response_model=ConversationForkRead,
    status_code=status.HTTP_201_CREATED,
)
async def fork_conversation(
    conversation_id: UUID,
    message_id: UUID,
    payload: ConversationForkCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationForkRead:
    # v1 只复制当前 visible path 中 root -> target message 这一段。
    return await ConversationService(session).fork_conversation(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        payload=payload,
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/cancel",
    response_model=MessageRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_message_generation(
    conversation_id: UUID,
    message_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MessageRead:
    # 取消接口只负责发出停止信号；真正的最终状态由流式生成协程落库。
    return await ConversationService(session).cancel_message_generation(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def encode_sse_event(event: ConversationStreamEvent) -> str:
    # SSE 事件之间用空行分隔，data 使用紧凑 JSON 方便前端逐事件解析。
    data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    sse_lines = []
    if event.event_id:
        sse_lines.append(f"id: {event.event_id}")
    sse_lines.append(f"event: {event.event}")
    sse_lines.append(f"data: {data}")
    return "\n".join(sse_lines) + "\n\n"

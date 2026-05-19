import asyncio
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import conversations as conversations_api
from app.api.v1 import files as files_api
from app.models.user import User
from app.schemas.conversation import MessageEdit
from app.services.conversation import ConversationService
from app.services.conversations import runs, stream_run


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_message(*, role: str, status: str = "completed", parent_message_id=None) -> Any:
    message_id = uuid4()
    return SimpleNamespace(
        id=message_id,
        conversation_id=uuid4(),
        sequence_no=1,
        langgraph_message_id=None,
        parent_message_id=parent_message_id,
        active_child_message_id=None,
        depth=0,
        source_message_id=None,
        revision_type="normal",
        role=role,
        content="hello" if role == "user" else "",
        reasoning_content="",
        content_parts=[],
        status=status,
        llm_config_id=uuid4() if role == "assistant" else None,
        provider="openai" if role == "assistant" else None,
        model="gpt-test" if role == "assistant" else None,
        token_usage={},
        response_metadata={},
        created_at="2026-05-12T00:00:00Z",
    )


def make_conversation(*, user_id=None, conversation_id=None) -> Any:
    return SimpleNamespace(
        id=conversation_id or uuid4(),
        user_id=user_id or uuid4(),
        thread_id="thread-test",
        title="Test",
        llm_config_id=uuid4(),
        chat_mode="chat",
        summary=None,
        summary_updated_at=None,
        summary_message_count=0,
        has_active_run=False,
        next_message_sequence_no=1,
        active_leaf_message_id=None,
        forked_from_conversation_id=None,
        forked_from_message_id=None,
        summary_leaf_message_id=None,
        metadata_={},
        created_at="2026-05-12T00:00:00Z",
        updated_at="2026-05-12T00:00:00Z",
    )


class FakeConversations:
    def __init__(self, conversation: Any) -> None:
        self.conversation = conversation
        self.touched = []

    async def get_active(self, *, user_id, conversation_id, for_update=False):
        if user_id != self.conversation.user_id or conversation_id != self.conversation.id:
            return None
        return self.conversation

    async def touch(self, conversation_id):
        self.touched.append(conversation_id)

    async def recompute_has_active_run(self, conversation_id):
        self.conversation.has_active_run = False
        return False


class FakeMessages:
    def __init__(self) -> None:
        self.created_user: Any = None
        self.created_assistant: Any = None
        self.target: Any = None
        self.parent: Any = None
        self.idempotent_assistant: Any = None
        self.failed_message: Any = None
        self.completed_message: Any = None
        self.partial_message: Any = None
        self.cancelled_message: Any = None

    async def get_by_id(self, *, conversation_id, message_id):
        if self.target and message_id == self.target.id:
            return self.target
        if self.parent and message_id == self.parent.id:
            return self.parent
        if self.created_user and message_id == self.created_user.id:
            return self.created_user
        if self.created_assistant and message_id == self.created_assistant.id:
            return self.created_assistant
        return None

    async def get_by_id_for_update(self, *, conversation_id, message_id):
        return await self.get_by_id(conversation_id=conversation_id, message_id=message_id)

    async def find_user_message_by_idempotency(self, **kwargs):
        return None

    async def find_assistant_message_by_idempotency(self, **kwargs):
        return self.idempotent_assistant

    async def create_user_message(self, **kwargs):
        parent_message = kwargs.get("parent_message")
        self.created_user = make_message(
            role="user",
            parent_message_id=parent_message.id if parent_message else None,
        )
        self.created_user.content = kwargs["content"]
        self.created_user.content_parts = kwargs.get("content_parts") or []
        self.created_user.source_message_id = kwargs.get("source_message_id")
        self.created_user.revision_type = kwargs.get("revision_type")
        return self.created_user

    async def create_assistant_placeholder(self, **kwargs):
        self.created_assistant = make_message(
            role="assistant", status="streaming", parent_message_id=kwargs["parent_message"].id
        )
        self.created_assistant.conversation_id = kwargs["conversation_id"]
        self.created_assistant.llm_config_id = kwargs["llm_config_id"]
        self.created_assistant.provider = kwargs["provider"]
        self.created_assistant.model = kwargs["model"]
        self.created_assistant.source_message_id = kwargs.get("source_message_id")
        self.created_assistant.revision_type = kwargs.get("revision_type")
        return self.created_assistant

    async def set_conversation_active_leaf(self, *, conversation, message):
        conversation.active_leaf_message_id = message.id if message else None

    async def get_message_read_state(self, message):
        return {
            "sibling_index": 1,
            "sibling_count": 1,
            "previous_sibling_id": None,
            "next_sibling_id": None,
        }

    async def resolve_active_leaf_from(self, *, conversation_id, message):
        return message

    async def list_path_to_message(self, *, conversation_id, message_id):
        if self.parent and message_id == self.parent.id:
            return [self.parent]
        return []

    async def fail_assistant_message(self, *, message, error):
        message.status = "failed"
        message.response_metadata = {"error": error}
        self.failed_message = message
        return message

    async def complete_assistant_message(self, *, message, content, reasoning_content, token_usage, response_metadata):
        message.status = "completed"
        message.content = content
        message.reasoning_content = reasoning_content
        message.token_usage = token_usage
        message.response_metadata = response_metadata
        self.completed_message = message
        return message

    async def partial_assistant_message(
        self, *, message, content, error, token_usage=None, response_metadata=None, reasoning_content=""
    ):
        message.status = "partial"
        message.content = content
        message.reasoning_content = reasoning_content
        message.token_usage = token_usage or {}
        message.response_metadata = {**(response_metadata or {}), "error": error}
        self.partial_message = message
        return message

    async def cancel_assistant_message(
        self, *, message, content, reasoning_content="", token_usage=None, response_metadata=None
    ):
        message.status = "cancelled"
        message.content = content
        message.reasoning_content = reasoning_content
        message.token_usage = token_usage or {}
        message.response_metadata = {**(response_metadata or {}), "error": "cancelled_by_user"}
        self.cancelled_message = message
        return message


class FakeLLMConfigs:
    async def get_active(self, *, user_id, config_id):
        return SimpleNamespace(
            id=config_id,
            provider="openai",
            models=["gpt-test"],
            is_enabled=True,
        )


class FakeSession:
    def __init__(self, conversation: Any | None = None) -> None:
        self.commits = 0
        self.conversation = conversation

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return None

    async def get(self, model, key):
        if self.conversation is not None and key == self.conversation.id:
            return self.conversation
        return None


class FakeStreamStore:
    def __init__(self) -> None:
        self.events = []
        self.stream_by_message = {}
        self.streams = {}
        self.cancelled = set()
        self.completed = []

    async def create_stream(self, *, stream_id, conversation_id, message_id, user_id):
        stream = SimpleNamespace(
            stream_id=stream_id,
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        self.streams[stream_id] = stream
        return stream

    async def append_event(self, stream_id, *, event, data):
        self.events.append((stream_id, event, data))

    async def get_stream_by_message_id(self, message_id):
        return self.stream_by_message.get(message_id)

    async def get_stream(self, stream_id):
        return self.streams.get(stream_id)

    async def attach_producer_task(self, stream_id, task=None):
        return None

    async def is_cancelled(self, stream_id):
        return stream_id in self.cancelled

    async def complete_stream(self, stream_id, *, retention_seconds):
        self.completed.append((stream_id, retention_seconds))


def make_service(conversation: Any, messages: FakeMessages) -> tuple[Any, FakeSession]:
    session = FakeSession(conversation)
    service = cast(Any, ConversationService(cast(Any, session)))
    service.conversations = FakeConversations(conversation)
    service.messages = messages
    service.llm_configs = FakeLLMConfigs()
    service._spawn_stream_producer = lambda **kwargs: None
    service._spawn_title_producer = lambda **kwargs: None
    return service, session


def test_non_stream_generation_routes_are_removed():
    # 生成类写入口只保留 SSE 版本，非流式路由不应再进入 OpenAPI/router。
    routes = {
        (next(iter(api_route.methods)), api_route.path)
        for api_route in (
            cast(APIRoute, route)
            for route in conversations_api.router.routes
            if isinstance(route, APIRoute)
        )
    }

    assert ("POST", "/conversations/{conversation_id}/messages") not in routes
    assert (
        "POST",
        "/conversations/{conversation_id}/messages/{message_id}/regenerate",
    ) not in routes
    assert ("POST", "/conversations/{conversation_id}/messages/{message_id}/edit") not in routes


def test_upload_to_conversation_checks_conversation_ownership_before_binding(monkeypatch):
    # 上传到已有会话时，必须先过 conversation 归属校验，再进入 FileService 绑定流程。
    conversation_id = uuid4()
    user = User(id=uuid4(), email="user@example.com", password_hash="hash")
    calls = {"checked": False, "uploaded": False}

    class GuardConversationService:
        def __init__(self, session):
            pass

        async def get_conversation(self, *, user_id, conversation_id):
            calls["checked"] = True
            raise HTTPException(status_code=404, detail="会话不存在")

    class GuardFileService:
        def __init__(self, session):
            pass

        async def upload_to_conversation(self, **kwargs):
            calls["uploaded"] = True

    monkeypatch.setattr(files_api, "ConversationService", GuardConversationService)
    monkeypatch.setattr(files_api, "FileService", GuardFileService)

    with pytest.raises(HTTPException):
        run(
            files_api.upload_conversation_file(
                conversation_id=conversation_id,
                file=UploadFile(filename="x.txt", file=BytesIO(b"x")),
                current_user=user,
                session=cast(AsyncSession, object()),
            )
        )

    assert calls == {"checked": True, "uploaded": False}


def test_stream_send_creates_placeholder_and_initial_event(monkeypatch):
    user_id = uuid4()
    conversation = make_conversation(user_id=user_id)
    messages = FakeMessages()
    service, _ = make_service(conversation, messages)
    store = FakeStreamStore()
    monkeypatch.setattr(runs, "conversation_stream_store", store)

    stream_id = run(
        service.start_stream_user_message(
            user_id=user_id,
            conversation_id=conversation.id,
            content="hello",
        )
    )

    assert stream_id == f"stream_{messages.created_assistant.id}"
    assert conversation.active_leaf_message_id == messages.created_assistant.id
    assert store.events[0][1] == "message.created"
    assert store.events[0][2]["user_message"]["id"] == str(messages.created_user.id)
    assert store.events[0][2]["assistant_message"]["id"] == str(messages.created_assistant.id)


def test_to_stream_event_refreshes_message_created_snapshot_state():
    user_id = uuid4()
    conversation = make_conversation(user_id=user_id)
    messages = FakeMessages()
    service, _ = make_service(conversation, messages)

    messages.created_user = make_message(role="user")
    messages.created_user.conversation_id = conversation.id
    messages.created_assistant = make_message(
        role="assistant",
        status="streaming",
        parent_message_id=messages.created_user.id,
    )
    messages.created_assistant.conversation_id = conversation.id

    async def fake_get_message_read_state(message):
        if message.id == messages.created_assistant.id:
            return {
                "thought_events": [{"type": "thought.reason", "phase": "analysis", "text": "live"}],
                "sibling_index": 1,
                "sibling_count": 2,
                "previous_sibling_id": None,
                "next_sibling_id": uuid4(),
            }
        return {
            "thought_events": [],
            "sibling_index": 1,
            "sibling_count": 1,
            "previous_sibling_id": None,
            "next_sibling_id": None,
        }

    messages.get_message_read_state = fake_get_message_read_state

    record = stream_run.StreamEventRecord(
        stream_id="stream-test",
        seq=1,
        event="message.created",
        data={
            "user_message": {
                "id": str(messages.created_user.id),
                "conversation_id": str(conversation.id),
                "sibling_count": 1,
            },
            "assistant_message": {
                "id": str(messages.created_assistant.id),
                "conversation_id": str(conversation.id),
                "sibling_count": 1,
                "thought_events": [],
            },
        },
    )

    event = run(service._to_stream_event(record))

    assert event.data["assistant_message"]["sibling_count"] == 2
    assert event.data["assistant_message"]["thought_events"] == [
        {"type": "thought.reason", "phase": "analysis", "text": "live"}
    ]


def test_stream_edit_with_files_binds_files_and_writes_content_parts(monkeypatch):
    user_id = uuid4()
    conversation = make_conversation(user_id=user_id)
    messages = FakeMessages()
    messages.target = make_message(role="user")
    service, _ = make_service(conversation, messages)
    store = FakeStreamStore()
    monkeypatch.setattr(runs, "conversation_stream_store", store)

    class FakeFileService:
        def __init__(self, session):
            pass

        async def bind_pending_files(self, *, user_id, file_ids, conversation_id):
            return [SimpleNamespace(id=file_ids[0], original_name="doc.txt")]

        async def wait_for_extraction(self, *, files):
            return files

        def build_content_parts(self, *, content, files):
            return [
                {"type": "text", "text": content},
                {"type": "file", "file_id": str(files[0].id)},
            ]

    import app.services.file_service as file_service_module

    monkeypatch.setattr(file_service_module, "FileService", FakeFileService)
    file_id = uuid4()

    run(
        service.start_stream_edit_user_message(
            user_id=user_id,
            conversation_id=conversation.id,
            message_id=messages.target.id,
            payload=MessageEdit(content="edited", file_ids=[file_id]),
        )
    )

    assert messages.created_user.source_message_id == messages.target.id
    assert messages.created_user.revision_type == "edit"
    assert messages.created_user.content_parts == [
        {"type": "text", "text": "edited"},
        {"type": "file", "file_id": str(file_id)},
    ]


def test_regenerate_idempotency_reuses_active_stream(monkeypatch):
    user_id = uuid4()
    conversation = make_conversation(user_id=user_id)
    messages = FakeMessages()
    messages.parent = make_message(role="user")
    messages.target = make_message(role="assistant", parent_message_id=messages.parent.id)
    messages.idempotent_assistant = make_message(
        role="assistant", status="streaming", parent_message_id=messages.parent.id
    )
    service, _ = make_service(conversation, messages)
    store = FakeStreamStore()
    store.stream_by_message[messages.idempotent_assistant.id] = SimpleNamespace(
        stream_id="stream-existing"
    )
    monkeypatch.setattr(runs, "conversation_stream_store", store)

    stream_id = run(
        service.start_stream_regenerate_assistant(
            user_id=user_id,
            conversation_id=conversation.id,
            message_id=messages.target.id,
            idempotency_key="regen-1",
        )
    )

    assert stream_id == "stream-existing"
    assert messages.created_assistant is None


def test_missing_runtime_stream_marks_streaming_assistant_failed(monkeypatch):
    user_id = uuid4()
    conversation = make_conversation(user_id=user_id)
    messages = FakeMessages()
    messages.target = make_message(role="assistant", status="streaming")
    service, _ = make_service(conversation, messages)
    store = FakeStreamStore()
    monkeypatch.setattr(stream_run, "conversation_stream_store", store)

    with pytest.raises(HTTPException):
        run(
            service.get_message_active_stream(
                user_id=user_id,
                conversation_id=conversation.id,
                message_id=messages.target.id,
            )
        )

    assert messages.failed_message is messages.target
    assert messages.target.status == "failed"


def test_produce_stream_emits_tool_call_delta_and_persists_aggregated_tool_calls(monkeypatch):
    user_id = uuid4()
    conversation = make_conversation(user_id=user_id)
    messages = FakeMessages()
    messages.parent = make_message(role="user")
    assistant_message = make_message(role="assistant", status="streaming", parent_message_id=messages.parent.id)
    assistant_message.conversation_id = conversation.id
    assistant_message.llm_config_id = uuid4()
    assistant_message.provider = "openai"
    assistant_message.model = "gpt-test"
    messages.created_assistant = assistant_message
    service, _ = make_service(conversation, messages)
    store = FakeStreamStore()
    stream_id = f"stream_{assistant_message.id}"
    run(
        store.create_stream(
            stream_id=stream_id,
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            user_id=user_id,
        )
    )
    monkeypatch.setattr(stream_run, "conversation_stream_store", store)

    async def fake_stream(**kwargs):
        yield SimpleNamespace(
            content_delta="",
            reasoning_delta="",
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "search_docs",
                    "args": "{\"query\":",
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
            token_usage={},
            response_metadata={},
            finish_reason=None,
        )
        yield SimpleNamespace(
            content_delta="result",
            reasoning_delta="",
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "search_docs",
                    "args": "\"langgraph\"}",
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
            token_usage={"input_tokens": 10, "output_tokens": 5},
            response_metadata={"provider_finish_reason": "stop"},
            finish_reason="stop",
        )
        yield SimpleNamespace(
            content_delta="",
            reasoning_delta="",
            tool_calls=[],
            tool_results=[
                {
                    "tool_call_id": "call_1",
                    "name": "search_docs",
                    "args": {"query": "langgraph"},
                    "output": "找到 3 篇相关文档。",
                    "is_error": False,
                }
            ],
            token_usage={},
            response_metadata={},
            finish_reason=None,
        )

    # Phase 1：tool/agent 模式走旧路径，设置 chat_mode="tool" 确保路由到 legacy
    conversation.chat_mode = "tool"
    service.llm_client = SimpleNamespace(stream=fake_stream)

    run(service._produce_stream(stream_id=stream_id, conversation_id=conversation.id))

    tool_call_events = [event for event in store.events if event[1] == "message.tool_call_delta"]
    assert len(tool_call_events) == 2
    tool_result_events = [event for event in store.events if event[1] == "message.tool_result"]
    assert len(tool_result_events) == 1
    assert messages.completed_message is assistant_message
    assert messages.completed_message.content == "result"
    assert messages.completed_message.response_metadata["normalized_tool_calls"] == [
        {
            "id": "call_1",
            "name": "search_docs",
            "args": "{\"query\":\"langgraph\"}",
            "index": 0,
            "type": "tool_call_chunk",
        }
    ]
    assert messages.completed_message.response_metadata["normalized_tool_results"] == [
        {
            "tool_call_id": "call_1",
            "name": "search_docs",
            "args": {"query": "langgraph"},
            "output": "找到 3 篇相关文档。",
            "is_error": False,
        }
    ]


def test_produce_stream_chat_mode_routes_to_langgraph_runtime(monkeypatch):
    user_id = uuid4()
    conversation = make_conversation(user_id=user_id)
    messages = FakeMessages()
    parent = make_message(role="user")
    assistant_message = make_message(
        role="assistant",
        status="streaming",
        parent_message_id=parent.id,
    )
    assistant_message.llm_config_id = uuid4()
    assistant_message.conversation_id = conversation.id
    messages.parent = parent
    messages.created_assistant = assistant_message
    store = FakeStreamStore()
    store.streams["stream-chat"] = SimpleNamespace(
        stream_id="stream-chat",
        conversation_id=conversation.id,
        message_id=assistant_message.id,
        user_id=user_id,
    )
    service, _ = make_service(conversation, messages)
    calls = {"runtime_used": False, "stream_called": False}

    async def fake_stream(**kwargs):
        calls["stream_called"] = True
        yield SimpleNamespace(
            content_delta="你好",
            reasoning_delta="思考",
            token_usage={"output_tokens": 2},
            response_metadata={"provider": "openai", "model": "gpt-test"},
            finish_reason="stop",
        )

    class FakeRuntime:
        def __init__(self, *, stream_factory, llm_invoke=None, tool_runtime=None):
            self._stream_factory = stream_factory

        async def run_stream(self, *, state, stream_adapter):
            calls["runtime_used"] = True
            async for chunk in self._stream_factory():
                if chunk.reasoning_delta:
                    await stream_adapter.emit_custom_event(
                        {"type": "reasoning_delta", "delta": chunk.reasoning_delta}
                    )
                if chunk.content_delta:
                    await stream_adapter.emit_custom_event(
                        {"type": "content_delta", "delta": chunk.content_delta}
                    )
                if chunk.token_usage:
                    await stream_adapter.emit_custom_event(
                        {"type": "token_usage", "usage": chunk.token_usage}
                    )
                if chunk.response_metadata:
                    await stream_adapter.emit_custom_event(
                        {"type": "response_metadata", "metadata": chunk.response_metadata}
                    )
                if chunk.finish_reason:
                    await stream_adapter.emit_custom_event(
                        {"type": "finish_reason", "finish_reason": chunk.finish_reason}
                    )
            return {
                **state,
                "response_text": "你好",
                "reasoning_text": "思考",
                "token_usage": {"output_tokens": 2},
                "response_metadata": {
                    "provider": "openai",
                    "model": "gpt-test",
                    "finish_reason": "stop",
                },
                "error": None,
            }

    service.llm_client = SimpleNamespace(
        stream=fake_stream,
        tool_runtime=SimpleNamespace(
            get_langchain_tools=lambda: [],
            register_tools=lambda tools: None,
        ),
    )
    monkeypatch.setattr(stream_run, "conversation_stream_store", store)
    monkeypatch.setattr(stream_run, "LangGraphChatRuntime", FakeRuntime)
    # 同时 patch stream_adapter 的 stream_store，避免 emit_custom_event 调用真实 store
    from app.services.langgraph_runtime import stream_adapter as sa_mod
    monkeypatch.setattr(sa_mod, "conversation_stream_store", store)

    run(service._produce_stream(stream_id="stream-chat", conversation_id=conversation.id))

    assert calls == {"runtime_used": True, "stream_called": True}
    assert messages.completed_message is assistant_message
    assert messages.completed_message.content == "你好"
    assert messages.completed_message.reasoning_content == "思考"
    assert messages.completed_message.response_metadata["finish_reason"] == "stop"
    assert any(event[1] == "message.delta" for event in store.events)
    assert any(event[1] == "message.reasoning_delta" for event in store.events)
    assert any(event[1] == "message.completed" for event in store.events)

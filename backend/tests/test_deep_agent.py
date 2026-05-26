"""WebAgent runtime 回归测试。"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

from app.services.langgraph_runtime.agent_types import AgentBudget
from app.services.langgraph_runtime.agent_workspace import (
    InMemoryAgentWorkspace,
    create_agent_workspace,
)
from app.services.langgraph_runtime.chat_runtime import LangGraphChatRuntime
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext, OrbitRuntimeRequest
from app.services.langgraph_runtime.thread_runtime_store import thread_runtime_store
from app.services.langgraph_runtime.web_agent.definition import WebAgentDefinition
from app.services.langgraph_runtime.web_agent.runtime import WebAgentRuntime
from app.services.llm_client import LLMStreamChunk
from app.services.tools.runtime import OrbitToolRuntime


def run(coro):
    return asyncio.run(coro)


def test_planning_prompt_uses_budget_values():
    definition = WebAgentDefinition(
        tool_runtime=OrbitToolRuntime(),
        workspace=InMemoryAgentWorkspace(run_id="run-1"),
        budget=AgentBudget(max_rounds=4, max_tool_calls=9, max_search_calls_per_round=3, timeout_seconds=45),
        user_query="测试预算提示词",
        history_messages=[HumanMessage(content="测试预算提示词")],
    )

    prompt = definition.build_planning_prompt()

    assert "最多 4 轮工具循环" in prompt
    assert "最多 9 次工具调用" in prompt
    assert "单轮最多 3 次 websearch" in prompt
    assert "总超时 45 秒" in prompt


def test_web_agent_runtime_emits_timeline_and_final_result():
    events: list[dict[str, Any]] = []

    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        if not enable_tools and system_prompt is None:
            user_text = str(messages[-1].content)
            if "制定一个简短的搜索和执行计划" in user_text:
                yield LLMStreamChunk(content_delta="先搜索。")
                return
            yield LLMStreamChunk(reasoning_delta="整理")
            yield LLMStreamChunk(content_delta="最终答案")
            return

        if enable_tools:
            yield LLMStreamChunk(
                tool_results=[
                    {
                        "name": "websearch",
                        "args": {"query": "orbit"},
                        "output": "搜索结果摘要",
                        "tool_call_id": "tool-1",
                        "is_error": False,
                    }
                ]
            )
            return

        if system_prompt:
            yield LLMStreamChunk(content_delta="已经拿到足够信息。")

    runtime = WebAgentRuntime(
        llm_invoke=fake_llm_invoke,
        tool_runtime=OrbitToolRuntime(),
        budget=AgentBudget(max_rounds=2, max_tool_calls=4, timeout_seconds=30),
    )

    result = run(
        runtime.run(
            user_query="Orbit 最近实现到了哪里？",
            history_messages=[HumanMessage(content="Orbit 最近实现到了哪里？")],
            runtime_context=_runtime_context(),
            on_event=events.append,
        )
    )

    assert result.error is None
    assert result.planning_text == "先搜索。"
    assert result.reasoning_text == "整理"
    assert result.final_content == "最终答案"
    assert {"path": "plan.md", "size": str(len("先搜索。"))} in result.workspace_files
    assert {"path": "notes.md", "size": str(len("## 第 1 轮\n已经拿到足够信息。\n\n### websearch\n搜索结果摘要"))} in result.workspace_files
    assert {"path": "final.md", "size": str(len("最终答案"))} in result.workspace_files
    assert any(event.get("type") == "thought.planning" for event in events)
    assert any(event.get("type") == "thought.tool" for event in events)
    assert any(event.get("type") == "thought.summary" for event in events)
    assert any(event.get("type") == "reasoning_delta" for event in events)
    assert any(event.get("type") == "content_delta" for event in events)


def test_thought_events_are_compacted_after_completion():
    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        if not enable_tools and system_prompt is None:
            user_text = str(messages[-1].content)
            if "制定一个简短的搜索和执行计划" in user_text:
                yield LLMStreamChunk(content_delta="先")
                yield LLMStreamChunk(content_delta="搜索")
                return
            yield LLMStreamChunk(reasoning_delta="整")
            yield LLMStreamChunk(reasoning_delta="理")
            yield LLMStreamChunk(content_delta="最终")
            yield LLMStreamChunk(content_delta="答案")
            return

        if enable_tools:
            yield LLMStreamChunk(
                tool_results=[
                    {
                        "name": "websearch",
                        "args": {"query": "orbit"},
                        "output": "搜索结果摘要",
                        "tool_call_id": "tool-1",
                        "is_error": False,
                    }
                ]
            )
            return

        if system_prompt:
            yield LLMStreamChunk(content_delta="已经拿到结果。")

    runtime = WebAgentRuntime(
        llm_invoke=fake_llm_invoke,
        tool_runtime=OrbitToolRuntime(),
        budget=AgentBudget(max_rounds=2, max_tool_calls=4, timeout_seconds=30),
    )

    result = run(
        runtime.run(
            user_query="Orbit 最近实现到了哪里？",
            history_messages=[HumanMessage(content="Orbit 最近实现到了哪里？")],
            runtime_context=_runtime_context(),
            on_event=lambda _event: None,
        )
    )

    planning_events = [event for event in result.thought_events if event["type"] == "thought.planning"]
    reason_events = [event for event in result.thought_events if event["type"] == "thought.reason"]
    assert len(planning_events) == 1
    assert planning_events[0]["text"] == "先搜索"
    assert len(reason_events) == 1
    assert reason_events[0]["text"] == "整理"


def test_single_round_websearch_limit_is_enforced():
    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        if not enable_tools and system_prompt is None:
            user_text = str(messages[-1].content)
            if "制定一个简短的搜索和执行计划" in user_text:
                yield LLMStreamChunk(content_delta="先搜索。")
                return
            yield LLMStreamChunk(content_delta="最终答案")
            return

        if enable_tools:
            yield LLMStreamChunk(
                tool_results=[
                    {"name": "websearch", "args": {"query": "a"}, "output": "a", "is_error": False},
                    {"name": "websearch", "args": {"query": "b"}, "output": "b", "is_error": False},
                ]
            )
            return

        if system_prompt:
            yield LLMStreamChunk(content_delta="继续搜索。")

    runtime = WebAgentRuntime(
        llm_invoke=fake_llm_invoke,
        tool_runtime=OrbitToolRuntime(),
        budget=AgentBudget(max_rounds=2, max_tool_calls=4, max_search_calls_per_round=1, timeout_seconds=30),
    )

    result = run(
        runtime.run(
            user_query="测试单轮搜索限制",
            history_messages=[HumanMessage(content="测试单轮搜索限制")],
            runtime_context=_runtime_context(),
            on_event=lambda _event: None,
        )
    )

    assert result.error is not None
    assert "单轮 websearch 次数超过上限" in result.error


def test_web_agent_runtime_stops_after_first_tool_round_in_each_step():
    events: list[dict[str, Any]] = []
    tool_rounds_seen = 0

    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        nonlocal tool_rounds_seen
        if not enable_tools and system_prompt is None:
            user_text = str(messages[-1].content)
            if "制定一个简短的搜索和执行计划" in user_text:
                yield LLMStreamChunk(content_delta="先搜索。")
                return
            yield LLMStreamChunk(content_delta="最终答案")
            return

        if enable_tools:
            tool_rounds_seen += 1
            yield LLMStreamChunk(
                tool_results=[
                    {
                        "name": "websearch",
                        "args": {"query": f"orbit-{tool_rounds_seen}"},
                        "output": f"第 {tool_rounds_seen} 轮结果",
                        "tool_call_id": f"tool-{tool_rounds_seen}",
                        "is_error": False,
                    }
                ]
            )
            # 如果 runtime 没有在拿到第一轮 tool_results 后及时 break，
            # 这里继续迭代就会抛错，复现线上“拿到工具结果却丢失 timeline”的问题。
            raise RuntimeError("不应继续消费到底层下一轮工具循环")

        if system_prompt:
            if "如果已有信息足够回答用户问题，只输出 `finalize`" in system_prompt:
                yield LLMStreamChunk(content_delta="finalize")
                return
            yield LLMStreamChunk(content_delta="这一轮已经够了。")

    runtime = WebAgentRuntime(
        llm_invoke=fake_llm_invoke,
        tool_runtime=OrbitToolRuntime(),
        budget=AgentBudget(max_rounds=1, max_tool_calls=4, timeout_seconds=30),
    )

    result = run(
        runtime.run(
            user_query="测试每个 step 只消费一轮工具结果",
            history_messages=[HumanMessage(content="测试每个 step 只消费一轮工具结果")],
            runtime_context=_runtime_context(),
            on_event=events.append,
        )
    )

    assert result.error is None
    assert tool_rounds_seen == 1
    assert any(event.get("type") == "thought.tool" for event in events)
    assert any(event.get("type") == "thought.summary" for event in events)


def test_create_agent_workspace_returns_in_memory_workspace():
    workspace = create_agent_workspace(run_id="run-1")

    assert isinstance(workspace, InMemoryAgentWorkspace)


def test_langgraph_chat_runtime_uses_shared_in_memory_checkpointer():
    async def fake_stream_factory():
        if False:
            yield None

    runtime_context = _runtime_context(chat_mode="chat")
    first = LangGraphChatRuntime(
        stream_factory=fake_stream_factory,
        runtime_context=runtime_context,
    )
    second = LangGraphChatRuntime(
        stream_factory=fake_stream_factory,
        runtime_context=runtime_context,
    )

    assert first._checkpointer is thread_runtime_store.get_checkpointer()
    assert second._checkpointer is thread_runtime_store.get_checkpointer()


def _runtime_context(
    thread_id: str = "thread-1",
    *,
    chat_mode: str = "agent",
) -> OrbitRuntimeContext:
    return OrbitRuntimeContext(
        request=OrbitRuntimeRequest(
            conversation_id="conv-1",
            assistant_message_id="msg-1",
            stream_id="stream-1",
            thread_id=thread_id,
            chat_mode=chat_mode,
            agent_type="web_agent" if chat_mode == "agent" else None,
            input_messages=[HumanMessage(content="test")],
            llm_config=None,
            model="test-model",
        ),
        tool_runtime=OrbitToolRuntime(),
        stream_writer=None,
    )

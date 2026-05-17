"""DeepAgent 关键收口回归测试。"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from app.services.langgraph_runtime.agent_types import AgentBudget
from app.services.langgraph_runtime.deep_agent import DeepAgent
from app.services.llm_client import LLMStreamChunk
from app.services.tools.runtime import OrbitToolRuntime


def run(coro):
    return asyncio.run(coro)


def test_tool_preamble_does_not_block_final_generation():
    """工具轮次中的引导语不应被当成最终答案，Phase 3 应能正常生成正文。"""
    calls: list[dict[str, object]] = []

    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        calls.append(
            {
                "enable_tools": enable_tools,
                "max_tool_rounds": max_tool_rounds,
            }
        )
        if not enable_tools:
            user_text = str(messages[-1].content)
            if "制定一个简短的搜索和执行计划" in user_text:
                yield LLMStreamChunk(content_delta="先搜索，再整理。")
                return
            yield LLMStreamChunk(content_delta="这是最终答案。")
            return

        yield LLMStreamChunk(content_delta="我来帮你搜索一下。")
        yield LLMStreamChunk(
            tool_results=[
                {
                    "name": "websearch",
                    "args": {"query": "orbit"},
                    "output": "搜索结果摘要",
                    "is_error": False,
                }
            ]
        )

    agent = DeepAgent(
        external_tools=[],
        tool_runtime=OrbitToolRuntime(),
        llm_invoke=fake_llm_invoke,
        budget=AgentBudget(max_rounds=2, max_tool_calls=4, timeout_seconds=30),
    )

    result = run(
        agent.run(
            user_query="Orbit 最近实现到了哪里？",
            history_messages=[HumanMessage(content="Orbit 最近实现到了哪里？")],
        )
    )

    assert result.error is None
    assert result.final_content == "这是最终答案。"
    assert "我来帮你搜索一下" not in result.final_content
    assert calls[1]["enable_tools"] is True
    assert calls[1]["max_tool_rounds"] == 2


def test_planning_prompt_uses_budget_values():
    """planning prompt 中的预算描述应来自 AgentBudget，而不是硬编码。"""
    captured_prompt = ""

    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        nonlocal captured_prompt
        captured_prompt = str(messages[-1].content)
        yield LLMStreamChunk(content_delta="先搜索，再整理。")

    agent = DeepAgent(
        external_tools=[],
        tool_runtime=OrbitToolRuntime(),
        llm_invoke=fake_llm_invoke,
        budget=AgentBudget(max_rounds=4, max_tool_calls=9, max_search_calls_per_round=3, timeout_seconds=45),
    )

    result = run(agent._phase_planning("测试预算提示词"))

    assert result == "先搜索，再整理。"
    assert "最多 4 轮工具循环" in captured_prompt
    assert "最多 9 次工具调用" in captured_prompt
    assert "单轮最多 3 次 websearch" in captured_prompt
    assert "总超时 45 秒" in captured_prompt


def test_agent_final_content_is_always_generated_in_phase3():
    """agent 模式下，最终用户可见正文应统一由 Phase 3 生成，以保证稳定流式输出。"""
    final_generation_calls = 0

    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        nonlocal final_generation_calls
        if not enable_tools:
            user_text = str(messages[-1].content)
            if "制定一个简短的搜索和执行计划" in user_text:
                yield LLMStreamChunk(content_delta="直接回答。")
                return
            final_generation_calls += 1
            yield LLMStreamChunk(content_delta="这是 Phase 3 的最终答案")
            return

        yield LLMStreamChunk(content_delta="42")

    agent = DeepAgent(
        external_tools=[],
        tool_runtime=OrbitToolRuntime(),
        llm_invoke=fake_llm_invoke,
        budget=AgentBudget(max_rounds=2, max_tool_calls=4, timeout_seconds=30),
    )

    result = run(
        agent.run(
            user_query="1+1 等于几？只回答数字。",
            history_messages=[HumanMessage(content="1+1 等于几？只回答数字。")],
        )
    )

    assert result.error is None
    assert result.final_content == "这是 Phase 3 的最终答案"
    assert final_generation_calls == 1


def test_compacted_thought_events_are_not_fragmented_after_completion():
    """持久化前应压缩连续 thought 事件，避免 completed 后又回到碎片列表。"""

    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        if not enable_tools:
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

        yield LLMStreamChunk(
            tool_results=[
                {
                    "name": "websearch",
                    "args": {"query": "orbit"},
                    "output": "搜索结果摘要",
                    "is_error": False,
                }
            ]
        )

    agent = DeepAgent(
        external_tools=[],
        tool_runtime=OrbitToolRuntime(),
        llm_invoke=fake_llm_invoke,
        budget=AgentBudget(max_rounds=2, max_tool_calls=4, timeout_seconds=30),
    )

    result = run(
        agent.run(
            user_query="Orbit 最近实现到了哪里？",
            history_messages=[HumanMessage(content="Orbit 最近实现到了哪里？")],
        )
    )

    planning_events = [e for e in result.thought_events if e["type"] == "thought.planning"]
    reason_events = [e for e in result.thought_events if e["type"] == "thought.reason"]
    assert len(planning_events) == 1
    assert planning_events[0]["text"] == "先搜索"
    assert len(reason_events) == 1
    assert reason_events[0]["text"] == "整理"


def test_single_round_websearch_limit_is_enforced():
    """单轮 websearch 次数超过 budget 时应被后端硬限制拦截。"""

    async def fake_llm_invoke(messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds):
        if not enable_tools:
            user_text = str(messages[-1].content)
            if "制定一个简短的搜索和执行计划" in user_text:
                yield LLMStreamChunk(content_delta="先搜索。")
                return
            return

        yield LLMStreamChunk(
            tool_results=[
                {"name": "websearch", "args": {"query": "a"}, "output": "a", "is_error": False},
                {"name": "websearch", "args": {"query": "b"}, "output": "b", "is_error": False},
            ]
        )

    agent = DeepAgent(
        external_tools=[],
        tool_runtime=OrbitToolRuntime(),
        llm_invoke=fake_llm_invoke,
        budget=AgentBudget(max_rounds=2, max_tool_calls=4, max_search_calls_per_round=1, timeout_seconds=30),
    )

    result = run(
        agent.run(
            user_query="测试单轮搜索限制",
            history_messages=[HumanMessage(content="测试单轮搜索限制")],
        )
    )

    assert result.error is not None
    assert "单轮 websearch 次数超过上限" in result.error

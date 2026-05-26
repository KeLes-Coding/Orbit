"""WebAgent runtime：Orbit 自定义 graph 的执行承载。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from app.services.langgraph_runtime.agent_contract import LlmInvoker
from app.services.langgraph_runtime.agent_types import AgentBudget, AgentExecutionResult
from app.services.langgraph_runtime.agent_workspace import (
    AgentWorkspace,
    create_agent_workspace,
)
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext
from app.services.langgraph_runtime.thread_runtime_store import thread_runtime_store
from app.services.langgraph_runtime.web_agent.definition import (
    _CONTINUE_DECISION_PROMPT,
    _LOOP_SUMMARY_PROMPT,
    WebAgentDefinition,
    WebAgentGraphState,
)
from app.services.langgraph_runtime.web_agent.projector import WebAgentProjector
from app.services.tools import OrbitToolRuntime


class WebAgentRuntime:
    """单次 WebAgent 执行运行时。"""

    def __init__(
        self,
        *,
        llm_invoke: LlmInvoker,
        tool_runtime: OrbitToolRuntime,
        budget: AgentBudget,
    ) -> None:
        self._llm_invoke = llm_invoke
        self._tool_runtime = tool_runtime
        self._budget = budget

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        """执行 WebAgent graph 并返回统一结果。"""
        workspace = create_agent_workspace(
            run_id=runtime_context.request.assistant_message_id or "default",
        )
        definition = WebAgentDefinition(
            tool_runtime=self._tool_runtime,
            workspace=workspace,
            budget=self._budget,
            user_query=user_query,
            history_messages=history_messages,
        )
        projector = WebAgentProjector(on_event=on_event)
        graph = self._build_graph(
            definition=definition,
            projector=projector,
            workspace=workspace,
        )
        config = {
            "configurable": {
                "thread_id": f"{runtime_context.request.thread_id or 'orbit-thread'}:web_agent"
            }
        }
        state = definition.build_initial_state()

        try:
            await asyncio.wait_for(graph.ainvoke(state, config=config), timeout=self._budget.timeout_seconds)
            snapshot = await graph.aget_state(config)
            values = snapshot.values if snapshot and snapshot.values else state
        except Exception as exc:
            return projector.build_result(
                planning_text=state.get("planning_text", ""),
                loop_summaries=state.get("loop_summaries", []),
                workspace=workspace,
                response_metadata=self._build_metadata(error=str(exc)),
                error=f"WebAgent 运行失败：{exc}",
            )

        return projector.build_result(
            planning_text=values.get("planning_text", ""),
            loop_summaries=values.get("loop_summaries", []),
            workspace=workspace,
            response_metadata=self._build_metadata(error=values.get("error")),
            error=values.get("error"),
        )

    def _build_graph(
        self,
        *,
        definition: WebAgentDefinition,
        projector: WebAgentProjector,
        workspace: AgentWorkspace,
    ):
        """构建 WebAgent graph。"""
        tools = definition.build_tools()
        builder = StateGraph(WebAgentGraphState)

        builder.add_node("prepare_context", self._prepare_context)
        builder.add_node("planning_node", self._planning_node(definition, projector, workspace))
        builder.add_node("execute_research_step", self._execute_research_step(definition, projector, tools, workspace))
        builder.add_node("decide_continue", self._decide_continue(definition, projector, workspace))
        builder.add_node("finalize_answer", self._finalize_answer(definition, projector, workspace))
        builder.add_node("project_result", self._project_result(workspace))

        builder.add_edge(START, "prepare_context")
        builder.add_edge("prepare_context", "planning_node")
        builder.add_edge("planning_node", "execute_research_step")
        builder.add_edge("execute_research_step", "decide_continue")
        builder.add_conditional_edges(
            "decide_continue",
            self._route_after_decision,
            {
                "continue": "execute_research_step",
                "finalize": "finalize_answer",
            },
        )
        builder.add_edge("finalize_answer", "project_result")
        builder.add_edge("project_result", END)
        return builder.compile(checkpointer=thread_runtime_store.get_checkpointer())

    @staticmethod
    def _prepare_context(_state: WebAgentGraphState) -> dict[str, Any]:
        """当前阶段只保留轻量 prepare 节点，为后续扩展留位。"""
        return {}

    def _planning_node(
        self,
        definition: WebAgentDefinition,
        projector: WebAgentProjector,
        workspace: AgentWorkspace,
    ):
        async def run(state: WebAgentGraphState) -> dict[str, Any]:
            parts: list[str] = []
            try:
                async for chunk in self._llm_invoke(
                    [HumanMessage(content=definition.build_planning_prompt())],
                    None,
                    False,
                    None,
                    None,
                    None,
                ):
                    if chunk.token_usage:
                        projector.merge_token_usage(chunk.token_usage)
                    if chunk.content_delta:
                        parts.append(chunk.content_delta)
                        projector.emit_thought(
                            event_type="thought.planning",
                            phase="planning",
                            text=chunk.content_delta,
                        )
            except Exception:
                return {"planning_text": ""}

            planning_text = "".join(parts).strip()
            if planning_text:
                await workspace.write_file("plan.md", planning_text)
            return {"planning_text": planning_text}

        return run

    def _execute_research_step(
        self,
        definition: WebAgentDefinition,
        projector: WebAgentProjector,
        tools,
        workspace: AgentWorkspace,
    ):
        async def run(state: WebAgentGraphState) -> dict[str, Any]:
            round_index = int(state.get("round_index", 0)) + 1
            total_tool_calls = int(state.get("total_tool_calls", 0))
            loop_summaries = list(state.get("loop_summaries", []))
            planning_text = state.get("planning_text", "")

            messages = list(state.get("history_messages", []))
            system_prompt = definition.build_tool_system_prompt(planning_text)

            tool_results: list[dict[str, Any]] = []
            saw_tool_round = False
            try:
                async for chunk in self._llm_invoke(
                    messages,
                    system_prompt,
                    True,
                    tools,
                    self._tool_runtime,
                    1,
                ):
                    if chunk.token_usage:
                        projector.merge_token_usage(chunk.token_usage)
                    if chunk.tool_results:
                        tool_results.extend(chunk.tool_results)
                        saw_tool_round = True
                        # 这里拿到的是一次完整的工具执行结果。
                        # 当前 step 的职责就是“执行一轮 research”，因此立即结束这一轮，
                        # 把后续是否继续搜索交给外层 graph 决策，而不是继续让底层
                        # tool loop 在单次 step 内自行滚动到下一轮。
                        break
            except Exception as exc:
                if saw_tool_round and tool_results:
                    # 某些 provider/tool loop 会在继续尝试下一轮时抛“超过上限”错误。
                    # 只要当前 step 已经拿到一轮完整工具结果，就按成功的一轮继续收口。
                    pass
                else:
                    return {"error": f"Agent 工具阶段异常：{exc}", "next_action": "finalize"}

            total_tool_calls += len(tool_results)
            if total_tool_calls > self._budget.max_tool_calls:
                return {
                    "error": f"工具调用次数超过上限（{self._budget.max_tool_calls}）",
                    "next_action": "finalize",
                }

            search_calls = sum(1 for item in tool_results if str(item.get("name") or "") == "websearch")
            if search_calls > self._budget.max_search_calls_per_round:
                return {
                    "error": f"单轮 websearch 次数超过上限（{self._budget.max_search_calls_per_round}）",
                    "next_action": "finalize",
                }

            if not tool_results:
                return {
                    "round_index": round_index,
                    "total_tool_calls": total_tool_calls,
                    "last_tool_results": [],
                    "next_action": "finalize",
                }

            for result in tool_results:
                projector.emit_thought(
                    event_type="thought.tool",
                    phase="loop",
                    text=f"调用工具：{result.get('name', 'unknown')}",
                    meta={
                        "tool": result.get("name", "unknown"),
                        "args": result.get("args", {}),
                        "tool_call_id": result.get("tool_call_id"),
                    },
                )

            summary_text = await self._summarize_tool_round(
                definition=definition,
                projector=projector,
                tool_round=round_index,
                tool_results=tool_results,
            )
            if not summary_text:
                summary_text = f"第 {round_index} 轮完成，共执行 {len(tool_results)} 个工具。"

            projector.emit_thought(
                event_type="thought.summary",
                phase="loop",
                text=summary_text,
                meta={"round": round_index, "tool_count": len(tool_results)},
            )
            await self._append_notes(
                workspace=workspace,
                tool_round=round_index,
                summary_text=summary_text,
                tool_results=tool_results,
            )
            loop_summaries.append({
                "step": round_index,
                "summary": summary_text,
                "tool_results": list(tool_results),
            })
            return {
                "round_index": round_index,
                "total_tool_calls": total_tool_calls,
                "last_tool_results": tool_results,
                "loop_summaries": loop_summaries,
                "next_action": "continue",
            }

        return run

    def _decide_continue(
        self,
        definition: WebAgentDefinition,
        projector: WebAgentProjector,
        workspace: AgentWorkspace,
    ):
        async def run(state: WebAgentGraphState) -> dict[str, Any]:
            if state.get("error"):
                return {"next_action": "finalize"}
            if not state.get("last_tool_results"):
                return {"next_action": "finalize"}
            if int(state.get("round_index", 0)) >= self._budget.max_rounds:
                return {"next_action": "finalize"}

            notes_content = workspace.get_content("notes.md") or ""
            parts: list[str] = []
            try:
                async for chunk in self._llm_invoke(
                    definition.build_decision_messages(
                        notes_content=notes_content,
                        planning_text=state.get("planning_text", ""),
                    ),
                    _CONTINUE_DECISION_PROMPT,
                    False,
                    None,
                    None,
                    None,
                ):
                    if chunk.token_usage:
                        projector.merge_token_usage(chunk.token_usage)
                    if chunk.content_delta:
                        parts.append(chunk.content_delta)
            except Exception:
                return {"next_action": "finalize"}

            decision_text = "".join(parts).strip().lower()
            next_action = "continue" if "continue" in decision_text else "finalize"
            return {"next_action": next_action}

        return run

    def _finalize_answer(
        self,
        definition: WebAgentDefinition,
        projector: WebAgentProjector,
        workspace: AgentWorkspace,
    ):
        async def run(_state: WebAgentGraphState) -> dict[str, Any]:
            notes_content = workspace.get_content("notes.md") or ""
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            try:
                async for chunk in self._llm_invoke(
                    definition.build_final_messages(notes_content=notes_content),
                    None,
                    False,
                    None,
                    None,
                    None,
                ):
                    if chunk.token_usage:
                        projector.merge_token_usage(chunk.token_usage)
                    if chunk.reasoning_delta:
                        reasoning_parts.append(chunk.reasoning_delta)
                        projector.emit_thought(
                            event_type="thought.reason",
                            phase="reason",
                            text=chunk.reasoning_delta,
                        )
                        projector.emit_reasoning_delta(chunk.reasoning_delta)
                    if chunk.content_delta:
                        content_parts.append(chunk.content_delta)
                        projector.emit_content_delta(chunk.content_delta)
            except Exception as exc:
                return {"error": f"Final generation 异常：{exc}"}

            final_content = "".join(content_parts).strip()
            reasoning_text = "".join(reasoning_parts)
            if not final_content:
                return {"error": "模型服务没有返回 assistant 内容"}
            await workspace.write_file("final.md", final_content)
            return {
                "final_content": final_content,
                "reasoning_text": reasoning_text,
            }

        return run

    def _project_result(self, workspace: AgentWorkspace):
        async def run(state: WebAgentGraphState) -> dict[str, Any]:
            # 这里显式读取一次文件索引，确保 project_result 节点完成最终收口。
            _ = workspace.get_file_index()
            return {
                "planning_text": state.get("planning_text", ""),
                "loop_summaries": state.get("loop_summaries", []),
                "error": state.get("error"),
            }

        return run

    @staticmethod
    def _route_after_decision(state: WebAgentGraphState) -> str:
        return state.get("next_action", "finalize")

    async def _summarize_tool_round(
        self,
        *,
        definition: WebAgentDefinition,
        projector: WebAgentProjector,
        tool_round: int,
        tool_results: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        try:
            async for chunk in self._llm_invoke(
                definition.build_summary_messages(
                    tool_round=tool_round,
                    tool_results=tool_results,
                ),
                _LOOP_SUMMARY_PROMPT,
                False,
                None,
                None,
                None,
            ):
                if chunk.token_usage:
                    projector.merge_token_usage(chunk.token_usage)
                if chunk.content_delta:
                    parts.append(chunk.content_delta)
        except Exception:
            return ""
        return "".join(parts).strip()

    @staticmethod
    async def _append_notes(
        *,
        workspace: AgentWorkspace,
        tool_round: int,
        summary_text: str,
        tool_results: list[dict[str, Any]],
    ) -> None:
        """把本轮结论与工具输出追加入 notes。"""
        existing = workspace.get_content("notes.md") or ""
        section = [existing.rstrip(), f"## 第 {tool_round} 轮", summary_text]
        for result in tool_results:
            section.append(f"\n### {result.get('name', 'unknown')}\n{result.get('output', '')}")
        await workspace.write_file("notes.md", "\n".join(item for item in section if item))

    def _build_metadata(self, *, error: str | None) -> dict[str, Any]:
        return {
            "agent_type": "web_agent",
            "runtime": "orbit_langgraph_web_agent",
            "error_present": bool(error),
        }


__all__ = [
    "WebAgentRuntime",
]

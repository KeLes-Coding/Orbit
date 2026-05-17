"""DeepAgent —— LLM 驱动的 agent harness。

DeepAgent 是 Phase 2 的核心执行引擎，负责：
1. Planning: 调 LLM 生成执行计划
2. Tool-calling loop: LLM 自主决定调用哪些工具，执行并总结结果
3. Final generation: LLM 生成 reasoning + 最终回答

预算控制（max rounds / max tool calls / timeout）是硬护栏，
执行流程由 LLM 自行决策，不预设搜索→抓取→总结的固定流水线。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool, tool

from app.services.langgraph_runtime.agent_types import AgentBudget, AgentEvent, AgentResult
from app.services.langgraph_runtime.agent_workspace import AgentWorkspace
from app.services.llm_client import LLMStreamChunk
from app.services.tools import OrbitToolRuntime

# llm_invoke: (messages, system_prompt, enable_tools, tools, tool_runtime, max_tool_rounds) -> stream
LlmInvoker = Callable[
    [list[BaseMessage], str | None, bool, list | None, Any | None, int | None],
    AsyncIterator[LLMStreamChunk],
]

AgentEventHandler = Callable[[dict[str, Any]], None]

_LOOP_SUMMARY_PROMPT = (
    "你是一个研究助手。下面是刚刚完成的一轮工具调用结果。"
    "请用中文输出 2-3 句简短总结："
    "1. 这一轮得到了什么关键信息；"
    "2. 这些信息对回答用户问题有什么帮助；"
    "3. 如有必要，陈述下一步还缺什么。"
    "只输出总结正文，不要使用标题，不要复述提示词。"
)


class DeepAgent:
    """LLM 驱动的 agent harness。

    通过 tool calling 让 LLM 自主决策执行步骤，预算控制防止失控。
    on_event 回调解耦事件消费（SSE / log / test），workspace 提供隔离文件存储。

    外部工具（websearch/webfetch）通过 external_tools + tool_runtime 注入，
    workspace 工具由 DeepAgent 内部创建并注册到 tool_runtime。
    """

    def __init__(
        self,
        *,
        external_tools: list[StructuredTool],
        tool_runtime: OrbitToolRuntime,
        llm_invoke: LlmInvoker,
        budget: AgentBudget | None = None,
        on_event: AgentEventHandler | None = None,
        workspace: AgentWorkspace | None = None,
    ) -> None:
        self._external_tools = external_tools
        self._tool_runtime = tool_runtime
        self._llm_invoke = llm_invoke
        self._budget = budget or AgentBudget()
        self._on_event_raw = on_event or (lambda _: None)
        self._workspace = workspace or AgentWorkspace(run_id="default")

        # 每轮 run() 重置
        self._thought_events: list[dict[str, Any]] = []
        self._loop_summaries: list[dict[str, Any]] = []
        self._token_usage: dict[str, Any] = {}
        self._planning_text: str = ""
        self._final_content: str = ""
        self._reasoning_text: str = ""
        self._error: str | None = None

    # ── 主入口 ─────────────────────────────────────────────────────

    async def run(
        self,
        user_query: str,
        history_messages: list[BaseMessage],
    ) -> AgentResult:
        """执行完整 agent 流程并返回聚合结果。"""
        try:
            result = await asyncio.wait_for(
                self._run_internal(user_query, history_messages),
                timeout=self._budget.timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._error = f"Agent 执行超时（{self._budget.timeout_seconds} 秒）"
        except Exception as exc:
            self._error = f"Agent 执行异常：{exc}"
        else:
            return result

        # 恢复：超时或异常后，尝试从 workspace 恢复已有内容
        recovered = self._workspace.get_content("final.md")
        if recovered and not self._final_content:
            self._final_content = recovered
        if self._final_content and self._error:
            # 有内容就不算完全失败
            pass
        return self._build_result()

    async def _run_internal(
        self,
        user_query: str,
        history_messages: list[BaseMessage],
    ) -> AgentResult:
        # 准备工具集：外部工具 + workspace 工具，注册到 runtime
        ws_tools = self._build_workspace_tools()
        self._tool_runtime.register_tools(ws_tools)
        all_tools = list(self._external_tools) + ws_tools

        # Phase 1: Planning
        self._planning_text = await self._phase_planning(user_query)

        if self._planning_text:
            try:
                await self._workspace.write_file("plan.md", self._planning_text)
            except Exception:
                pass

        # Phase 2: Agent tool-calling loop
        await self._phase_agent_loop(user_query, history_messages, all_tools)

        # 恢复：尝试从 workspace 获取已写入的最终内容
        recovered = self._workspace.get_content("final.md")
        if recovered and len(recovered) > len(self._final_content):
            self._final_content = recovered
            if self._error:
                self._error = None  # 恢复成功，清除错误

        # Phase 3: Final generation 统一承担用户可见正文输出。
        # Phase 2 只负责工具循环和中间态，不直接向用户流式输出最终回答，
        # 这样可以避免“先输出一段、又继续调工具”的流式协议冲突。
        tool_error = self._error
        self._error = None
        self._final_content = ""
        self._reasoning_text = ""
        await self._phase_final_generation(user_query, history_messages)
        if self._final_content:
            self._error = None
        elif tool_error:
            self._error = tool_error

        return self._build_result()

    # ── workspace 工具构建 ─────────────────────────────────────────

    def _build_workspace_tools(self) -> list[StructuredTool]:
        ws = self._workspace

        @tool
        async def write_file(path: str, content: str) -> str:
            """将内容写入工作区文件。path: 文件名(plan.md/notes.md/final.md); content: 要写入的内容"""
            return await ws.write_file(path, content)

        @tool
        async def read_file(path: str) -> str:
            """读取工作区文件内容。path: 文件名(plan.md/notes.md/final.md)"""
            return await ws.read_file(path)

        @tool
        async def list_files() -> str:
            """列出工作区中的所有文件"""
            return await ws.list_files()

        return [
            cast(StructuredTool, write_file),
            cast(StructuredTool, read_file),
            cast(StructuredTool, list_files),
        ]

    # ── Phase 1: Planning ─────────────────────────────────────────

    async def _phase_planning(self, user_query: str) -> str:
        """调 LLM 生成简短执行计划。失败不阻塞主流程。"""
        parts: list[str] = []
        planning_prompt = self._build_planning_prompt(user_query)
        try:
            async for chunk in self._llm_invoke(
                [HumanMessage(content=planning_prompt)],
                None,  # system_prompt
                False,  # no tools
                None,  # no tools list
                None,  # default tool_runtime
                None,  # no max_tool_rounds
            ):
                if chunk.content_delta:
                    parts.append(chunk.content_delta)
                    self._emit("thought.planning", "planning", chunk.content_delta)
        except Exception:
            return ""
        return "".join(parts).strip()

    # ── Phase 2+3: Agent loop + Final generation ─────────────────

    async def _phase_agent_loop(
        self,
        user_query: str,
        history_messages: list[BaseMessage],
        all_tools: list[StructuredTool],
    ) -> None:
        """合并 agent 工具循环与最终生成。

        LLMClient 的 _astream_with_tool_loop 处理多轮工具调用，
        本方法观察 stream chunk 中的 tool_calls / tool_results 发射 thought 事件，
        同时累积最终回答的 reasoning 和 content。
        """
        messages: list[BaseMessage] = list(history_messages)

        system_text = self._build_agent_system_prompt()
        if self._planning_text:
            system_text += f"\n\n你的执行计划：\n{self._planning_text}"

        # 如果历史第一条已是 SystemMessage（来自 summary），在其内容前追加；
        # 否则插入新的 SystemMessage
        if messages and isinstance(messages[0], SystemMessage):
            existing = messages[0].content
            messages[0] = SystemMessage(content=f"{system_text}\n\n---\n\n{existing}")
        else:
            messages.insert(0, SystemMessage(content=system_text))

        tool_round = 0
        total_tool_calls = 0

        try:
            async for chunk in self._llm_invoke(
                messages,
                None,  # system prompt already in messages
                True,  # enable tools
                all_tools,  # pass workspace + external tools
                self._tool_runtime,  # pass runtime with workspace tools registered
                self._budget.max_rounds,
            ):
                if chunk.token_usage:
                    self._merge_token_usage(chunk.token_usage)

                if chunk.tool_results:
                    tool_round += 1
                    results = chunk.tool_results
                    total_tool_calls += len(results)
                    if total_tool_calls > self._budget.max_tool_calls:
                        raise RuntimeError(
                            f"工具调用次数超过上限（{self._budget.max_tool_calls}）"
                        )
                    search_calls_this_round = sum(
                        1 for r in results if str(r.get("name") or "").strip() == "websearch"
                    )
                    if search_calls_this_round > self._budget.max_search_calls_per_round:
                        raise RuntimeError(
                            "单轮 websearch 次数超过上限"
                            f"（{self._budget.max_search_calls_per_round}）"
                        )
                    success_count = sum(1 for r in results if not r.get("is_error"))
                    error_count = sum(1 for r in results if r.get("is_error"))

                    # 发射已完成的工具调用（tool_results 包含完整工具名和参数）
                    for tr in results:
                        self._emit(
                            "thought.tool",
                            "loop",
                            f"调用工具：{tr.get('name', 'unknown')}",
                            {"tool": tr.get("name", "unknown"), "args": tr.get("args", {})},
                        )

                    summary_text = await self._summarize_tool_round(
                        user_query=user_query,
                        tool_round=tool_round,
                        results=results,
                    )
                    if not summary_text:
                        summary_text = f"第 {tool_round} 轮完成"
                        if success_count > 0:
                            summary_text += f"，{success_count} 个成功"
                        if error_count > 0:
                            summary_text += f"，{error_count} 个失败"

                    self._emit("thought.summary", "loop", summary_text, {
                        "round": tool_round,
                        "success_count": success_count,
                        "error_count": error_count,
                    })

                    self._loop_summaries.append({
                        "step": tool_round,
                        "summary": summary_text,
                        "tool_results": list(results),
                    })

                    try:
                        existing = await self._workspace.read_file("notes.md")
                        if existing.startswith("文件"):
                            existing = ""
                        existing += f"\n## 第 {tool_round} 轮\n{summary_text}\n"
                        # 写入实际的工具结果内容
                        for tr in results:
                            existing += f"\n### {tr.get('name', 'unknown')}\n{tr.get('output', '')}\n"
                        await self._workspace.write_file("notes.md", existing)
                    except Exception:
                        pass

                if chunk.reasoning_delta:
                    self._emit("thought.reason", "reason", chunk.reasoning_delta)

        except Exception as exc:
            self._error = f"Agent 执行异常：{exc}"
            return

    # ── Phase 3: Final generation ────────────────────────────────────

    async def _phase_final_generation(
        self,
        user_query: str,
        history_messages: list[BaseMessage],
    ) -> None:
        """工具循环结束后的最终生成阶段。

        用不带工具的 LLM 调用，基于已收集的 workspace 和对话上下文
        生成带 reasoning 的最终回答。
        """
        from langchain_core.messages import SystemMessage as SysMsg

        messages: list[BaseMessage] = list(history_messages)

        # 构建 final generation 的系统提示，包含已收集的 workspace 内容
        notes_content = self._workspace.get_content("notes.md") or ""
        system_text = (
            "你是一个研究助手。以下是你通过搜索和浏览收集到的信息：\n\n"
        )
        if notes_content:
            system_text += notes_content
        system_text += (
            f"\n\n用户原始问题：{user_query}\n\n"
            "请基于以上收集的信息，给出详细、有依据的中文回答。"
        )

        if messages and isinstance(messages[0], SystemMessage):
            existing = messages[0].content
            messages[0] = SysMsg(content=f"{system_text}\n\n---\n\n{existing}")
        else:
            messages.insert(0, SysMsg(content=system_text))

        content_parts: list[str] = []
        reasoning_parts: list[str] = []

        try:
            async for chunk in self._llm_invoke(
                messages,
                None,  # system prompt already in messages
                False,  # no tools
                None,   # no tools list
                None,   # default tool_runtime
                None,   # no max_tool_rounds
            ):
                if chunk.token_usage:
                    self._merge_token_usage(chunk.token_usage)

                if chunk.reasoning_delta:
                    reasoning_parts.append(chunk.reasoning_delta)
                    self._emit("thought.reason", "reason", chunk.reasoning_delta)
                    self._emit_raw({"type": "reasoning_delta", "delta": chunk.reasoning_delta})

                if chunk.content_delta:
                    content_parts.append(chunk.content_delta)
                    self._emit_raw({"type": "content_delta", "delta": chunk.content_delta})
        except Exception as exc:
            self._error = f"Final generation 异常：{exc}"
            return

        self._final_content = "".join(content_parts)
        self._reasoning_text = "".join(reasoning_parts)

        if self._final_content:
            try:
                await self._workspace.write_file("final.md", self._final_content)
            except Exception:
                pass

    async def _summarize_tool_round(
        self,
        *,
        user_query: str,
        tool_round: int,
        results: list[dict[str, Any]],
    ) -> str:
        """用一次轻量 LLM 调用总结当前工具轮的收获。"""
        lines = [f"用户问题：{user_query}", f"当前是第 {tool_round} 轮工具调用结果："]
        for idx, result in enumerate(results, start=1):
            lines.append(f"{idx}. 工具：{result.get('name', 'unknown')}")
            lines.append(f"参数：{result.get('args', {})}")
            lines.append(f"是否失败：{bool(result.get('is_error'))}")
            output = str(result.get("output", "") or "").strip()
            if len(output) > 4000:
                output = output[:4000] + "\n...[内容已截断]"
            lines.append(f"输出：{output}")

        try:
            parts: list[str] = []
            async for chunk in self._llm_invoke(
                [HumanMessage(content="\n".join(lines))],
                _LOOP_SUMMARY_PROMPT,
                False,
                None,
                None,
                None,
            ):
                if chunk.token_usage:
                    self._merge_token_usage(chunk.token_usage)
                if chunk.content_delta:
                    parts.append(chunk.content_delta)
                    self._emit(
                        "thought.summary",
                        "loop",
                        chunk.content_delta,
                        {"round": tool_round},
                    )
            return "".join(parts).strip()
        except Exception:
            return ""

    # ── helpers ───────────────────────────────────────────────────

    def _emit(self, type_: str, phase: str = "", text: str = "", meta: dict[str, Any] | None = None) -> None:
        event: AgentEvent = {
            "type": type_,
            "phase": phase,
            "text": text,
            "meta": meta or {},
        }
        payload = {
            "type": event["type"],
            "phase": event["phase"],
            "text": event["text"],
            "meta": event["meta"],
        }
        self._thought_events.append(payload)
        self._on_event_raw(payload)

    def _emit_raw(self, event: dict[str, Any]) -> None:
        self._on_event_raw(event)

    def _build_planning_prompt(self, user_query: str) -> str:
        return (
            "请为以下用户问题制定一个简短的搜索和执行计划。"
            "用 2-4 句话描述你打算如何查找信息、组织回答。"
            "只输出计划文本，不要开始搜索。"
            f"\n\n执行预算："
            f"\n- 最多 {self._budget.max_rounds} 轮工具循环"
            f"\n- 最多 {self._budget.max_tool_calls} 次工具调用"
            f"\n- 单轮最多 {self._budget.max_search_calls_per_round} 次工具调用"
            f"\n\n用户问题：{user_query}"
        )

    def _build_agent_system_prompt(self) -> str:
        return (
            "你是一个具备搜索和研究能力的 AI 助手。"
            "你可以使用 websearch 搜索信息，使用 webfetch 抓取网页内容。"
            "\n\n重要规则："
            f"\n1. 最多执行 {self._budget.max_rounds} 轮工具循环，然后必须停止工具调用"
            f"\n2. 全过程最多执行 {self._budget.max_tool_calls} 次工具调用"
            f"\n3. 单轮最多执行 {self._budget.max_search_calls_per_round} 次 websearch"
            "\n4. 收集到足够信息后，停止工具调用；最终回答由后续总结阶段统一生成"
            "\n5. 如果搜索结果已经足够回答问题，就不要继续做多余搜索"
            "\n6. 工具调用要节制，优先选择最相关的来源"
        )

    def _merge_token_usage(self, usage: dict[str, Any]) -> None:
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                self._token_usage[key] = self._token_usage.get(key, 0) + value
            else:
                self._token_usage[key] = value

    def _compact_thought_events(self) -> list[dict[str, Any]]:
        """压缩连续同阶段 thought 事件，避免完成后持久化为碎片列表。"""
        compacted: list[dict[str, Any]] = []
        for raw in self._thought_events:
            event = {
                "type": raw.get("type", ""),
                "phase": raw.get("phase", ""),
                "text": raw.get("text", ""),
                "meta": raw.get("meta", {}),
            }
            if not compacted:
                compacted.append(event)
                continue

            previous = compacted[-1]
            same_type = previous.get("type") == event.get("type")
            same_phase = previous.get("phase") == event.get("phase")
            if not (same_type and same_phase):
                compacted.append(event)
                continue

            event_type = str(event.get("type") or "")
            if event_type == "thought.tool":
                compacted.append(event)
                continue
            if event_type == "thought.summary":
                prev_round = (previous.get("meta") or {}).get("round")
                next_round = (event.get("meta") or {}).get("round")
                if prev_round != next_round:
                    compacted.append(event)
                    continue

            previous["text"] = f"{previous.get('text', '')}{event.get('text', '')}"
            if event.get("meta"):
                previous["meta"] = event["meta"]

        return compacted

    def _build_result(self) -> AgentResult:
        return AgentResult(
            planning_text=self._planning_text,
            loop_summaries=list(self._loop_summaries),
            reasoning_text=self._reasoning_text,
            final_content=self._final_content,
            thought_events=self._compact_thought_events(),
            workspace_files=self._workspace.get_file_index(),
            token_usage=dict(self._token_usage),
            error=self._error,
        )

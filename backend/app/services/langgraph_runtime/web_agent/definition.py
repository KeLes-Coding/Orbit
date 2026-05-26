"""WebAgent definition：定义状态、工具和提示词。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict, cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool, tool

from app.services.langgraph_runtime.agent_types import AgentBudget
from app.services.langgraph_runtime.agent_workspace import AgentWorkspace
from app.services.langgraph_runtime.web_agent.prompt import WEB_AGENT_PROMPT_GUIDANCE
from app.services.langgraph_runtime.web_agent.tools import build_web_agent_tools
from app.services.tools import OrbitToolRuntime

_LOOP_SUMMARY_PROMPT = (
    "请把本轮工具结果总结成一段简短中文。"
    "重点说明拿到了什么信息、是否已经足够回答用户问题。"
    "不要重复贴长原文。"
)

_CONTINUE_DECISION_PROMPT = (
    "你要判断当前信息是否还需要继续搜索。"
    "如果已有信息足够回答用户问题，只输出 `finalize`。"
    "如果还需要继续搜索或抓取更多资料，只输出 `continue`。"
    "不要输出其他内容。"
)


class WebAgentGraphState(TypedDict, total=False):
    """WebAgent graph 内部状态。"""

    user_query: str
    history_messages: list[BaseMessage]
    planning_text: str
    loop_summaries: list[dict[str, Any]]
    round_index: int
    total_tool_calls: int
    last_tool_results: list[dict[str, Any]]
    next_action: str
    reasoning_text: str
    final_content: str
    token_usage: dict[str, Any]
    error: str | None


@dataclass
class WebAgentDefinition:
    """单个 WebAgent 执行定义。"""

    tool_runtime: OrbitToolRuntime
    workspace: AgentWorkspace
    budget: AgentBudget
    user_query: str
    history_messages: list[BaseMessage]

    def build_tools(self) -> list[StructuredTool]:
        """组装 web 工具和 workspace 工具。"""
        tools = build_web_agent_tools(self.tool_runtime)
        workspace_tools = self._build_workspace_tools()
        self.tool_runtime.register_tools(workspace_tools)
        return [*tools, *workspace_tools]

    def build_initial_state(self) -> WebAgentGraphState:
        """构造 graph 初始状态。"""
        return {
            "user_query": self.user_query,
            "history_messages": list(self.history_messages),
            "planning_text": "",
            "loop_summaries": [],
            "round_index": 0,
            "total_tool_calls": 0,
            "last_tool_results": [],
            "next_action": "continue",
            "reasoning_text": "",
            "final_content": "",
            "token_usage": {},
            "error": None,
        }

    def build_planning_prompt(self) -> str:
        """生成 planning 提示词。"""
        return (
            "请为以下用户问题制定一个简短的搜索和执行计划。"
            "用 2-4 句话描述你打算如何查找信息、组织回答。"
            "只输出计划文本，不要开始搜索。"
            f"\n\n执行预算："
            f"\n- 最多 {self.budget.max_rounds} 轮工具循环"
            f"\n- 最多 {self.budget.max_tool_calls} 次工具调用"
            f"\n- 单轮最多 {self.budget.max_search_calls_per_round} 次 websearch"
            f"\n- 总超时 {int(self.budget.timeout_seconds)} 秒"
            f"\n\n用户问题：{self.user_query}"
        )

    def build_tool_system_prompt(self, planning_text: str) -> str:
        """生成工具阶段系统提示词。"""
        prompt = (
            f"{WEB_AGENT_PROMPT_GUIDANCE}\n\n"
            "你正在执行一轮 research。"
            "优先调用最少但最有效的工具，不要输出最终回答。"
            f"\n\n约束："
            f"\n1. 当前总共最多 {self.budget.max_rounds} 轮 research"
            f"\n2. 全过程最多 {self.budget.max_tool_calls} 次工具调用"
            f"\n3. 单轮最多 {self.budget.max_search_calls_per_round} 次 websearch"
            "\n4. 如果已有信息足够回答问题，这一轮就停止继续搜索"
            "\n5. notes.md 用于沉淀中间结论，final.md 仅在最终回答阶段写入"
        )
        if planning_text:
            prompt += f"\n\n当前计划：\n{planning_text}"
        return prompt

    def build_summary_messages(
        self,
        *,
        tool_round: int,
        tool_results: list[dict[str, Any]],
    ) -> list[BaseMessage]:
        """构造工具轮摘要请求。"""
        lines = [f"用户问题：{self.user_query}", f"当前是第 {tool_round} 轮工具调用结果："]
        for index, result in enumerate(tool_results, start=1):
            output = str(result.get("output", "") or "").strip()
            if len(output) > 4000:
                output = f"{output[:4000]}\n...[内容已截断]"
            lines.extend([
                f"{index}. 工具：{result.get('name', 'unknown')}",
                f"参数：{result.get('args', {})}",
                f"是否失败：{bool(result.get('is_error'))}",
                f"输出：{output}",
            ])
        return [HumanMessage(content="\n".join(lines))]

    def build_decision_messages(self, *, notes_content: str, planning_text: str) -> list[BaseMessage]:
        """构造是否继续 research 的判断请求。"""
        content = (
            f"用户问题：{self.user_query}\n\n"
            f"执行计划：\n{planning_text or '无'}\n\n"
            f"当前 notes：\n{notes_content or '无'}"
        )
        return [HumanMessage(content=content)]

    def build_final_messages(self, *, notes_content: str) -> list[BaseMessage]:
        """构造最终回答阶段消息。"""
        messages = list(self.history_messages)
        system_text = (
            "你是一个研究助手。以下是你通过搜索和浏览收集到的信息：\n\n"
            f"{notes_content or '暂无 notes。'}"
            f"\n\n用户原始问题：{self.user_query}\n\n"
            "请基于以上信息，给出清晰、可靠、结构化的中文回答。"
        )
        if messages and isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=f"{system_text}\n\n---\n\n{messages[0].content}")
        else:
            messages.insert(0, SystemMessage(content=system_text))
        return messages

    def _build_workspace_tools(self) -> list[StructuredTool]:
        """为当前 run 构造局部 workspace 工具。"""
        workspace = self.workspace

        @tool
        async def write_file(path: str, content: str) -> str:
            """将内容写入工作区文件。path 仅允许 plan.md / notes.md / final.md。"""
            return await workspace.write_file(path, content)

        @tool
        async def read_file(path: str) -> str:
            """读取工作区文件内容。path 仅允许 plan.md / notes.md / final.md。"""
            return await workspace.read_file(path)

        @tool
        async def list_files() -> str:
            """列出工作区中的所有文件。"""
            return await workspace.list_files()

        return [
            cast(StructuredTool, write_file),
            cast(StructuredTool, read_file),
            cast(StructuredTool, list_files),
        ]


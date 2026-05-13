"""LangGraph 流事件 → 统一流事件适配器。

将 deep agent（LangGraph CompiledStateGraph）的 astream 输出
转换为 UnifiedStreamEvent 序列，供上层 SSE 层消费。

LangGraph stream_mode=["messages", "updates"] 产出的事件类型：
  - (message, metadata) 元组：LLM token/消息
  - dict：节点更新（包含 tool_call / tool_result / todo / subagent 等）

支持的事件协议：
  —— 通用事件（所有 runtime 共用）——
  message.delta / message.reasoning_delta / message.completed
  message.failed / message.cancelled

  —— Agent 扩展事件（message.agent_delta 的 type 子类型）——
  tool_call / tool_result / todo

  —— Subagent 事件 ——
  agent.subagent.started / agent.subagent.delta / agent.subagent.completed

  —— HITL 事件（由上层 stream_run.py 在 GraphInterrupt 异常中产出）——
  agent.run.interrupted / agent.run.awaiting_approval / agent.run.resumed
"""
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage

from app.services.runtime.types import UnifiedStreamEvent

# deepagents 的标准节点名称（非 subagent 节点），用于识别 subagent 节点。
_STANDARD_NODE_NAMES = {
    "agent", "call_model", "tools", "pre_model_hook", "summarization",
    "__start__", "__end__", "planner", "todo", "review", "update_todos",
}


async def adapt_langgraph_stream(
    agent,
    messages: list,
    thread_id: str,
    assistant_message_id: UUID,
) -> AsyncIterator[UnifiedStreamEvent]:
    """将 deep agent 的 astream 输出转换为 UnifiedStreamEvent 序列。

    Parameters
    ----------
    agent: CompiledStateGraph
        已编译的 deep agent。
    messages: list[BaseMessage]
        历史消息列表（LangChain 格式）。
    thread_id: str
        LangGraph thread id，对应 Conversation.thread_id。
    assistant_message_id: UUID
        当前 assistant 占位消息的 ID，用于事件关联。
    """
    final_content_parts: list[str] = []
    emitted_deltas: set[str] = set()
    stream_config = {"configurable": {"thread_id": thread_id}}
    active_subagent: str | None = None  # 当前活跃的 subagent 名称

    async for event in agent.astream(
        {"messages": messages},
        config=stream_config,
        stream_mode=["messages", "updates"],
    ):
        event_kind, event_payload = _normalize_stream_event(event)
        if event_kind == "messages" and isinstance(event_payload, tuple):
            # (message, metadata) 格式：LLM token 或完整消息
            msg, _metadata = event_payload
            msg_type = getattr(msg, "type", "")

            if msg_type in ("AIMessageChunk", "ai"):
                delta = _extract_text_content(msg)
                if delta:
                    if _should_dedupe_message(msg):
                        message_key = _message_dedupe_key(msg)
                        if message_key in emitted_deltas:
                            continue
                        emitted_deltas.add(message_key)
                        final_content_parts.append(delta)
                        yield UnifiedStreamEvent(
                            event="message.delta",
                            data={
                                "message_id": str(assistant_message_id),
                                "delta": delta,
                            },
                        )
            elif msg_type in ("ToolMessage", "tool"):
                tool_output = _extract_text_content(msg)
                tool_call_id = getattr(msg, "tool_call_id", "")
                yield UnifiedStreamEvent(
                    event="message.agent_delta",
                    data={
                        "message_id": str(assistant_message_id),
                        "type": "tool_result",
                        "tool_call_id": tool_call_id,
                        "content": tool_output,
                    },
                )

        elif event_kind == "updates" and isinstance(event_payload, dict):
            # 节点更新：遍历每个节点的输出
            for node_name, node_output in event_payload.items():
                if not isinstance(node_output, dict):
                    continue

                # —— subagent 生命周期检测 ——
                # 节点名不在标准列表中时，视为 subagent 节点。
                is_subagent = (
                    node_name not in _STANDARD_NODE_NAMES
                    and not node_name.startswith("__")
                )
                if is_subagent:
                    # subagent 首次出现 → started 事件
                    if active_subagent != node_name:
                        active_subagent = node_name
                        yield UnifiedStreamEvent(
                            event="agent.subagent.started",
                            data={
                                "message_id": str(assistant_message_id),
                                "subagent_name": node_name,
                            },
                        )

                    # 检查 subagent 是否有文本输出
                    subagent_messages = node_output.get("messages", [])
                    if isinstance(subagent_messages, list):
                        for sm in subagent_messages:
                            delta = _extract_text_content(sm)
                            if delta:
                                yield UnifiedStreamEvent(
                                    event="agent.subagent.delta",
                                    data={
                                        "message_id": str(assistant_message_id),
                                        "subagent_name": node_name,
                                        "delta": delta,
                                    },
                                )

                    # 如果该 subagent 的输出包含最终结果，视为 completed
                    if node_output.get("output") or (
                        isinstance(subagent_messages, list)
                        and any(hasattr(m, "type") and m.type == "ai" for m in subagent_messages if hasattr(m, "type"))
                    ):
                        yield UnifiedStreamEvent(
                            event="agent.subagent.completed",
                            data={
                                "message_id": str(assistant_message_id),
                                "subagent_name": node_name,
                                "result": node_output.get("output", ""),
                            },
                        )
                        active_subagent = None

                node_messages = node_output.get("messages", [])
                if not isinstance(node_messages, list):
                    node_messages = [node_messages] if node_messages else []

                for m in node_messages:
                    # 工具调用（AIMessage 中携带 tool_calls）
                    # tool_call_id 让前端能将 tool_call 与后续的 tool_result 配对。
                    if isinstance(m, AIMessage) and hasattr(m, "tool_calls") and m.tool_calls:
                        for tc in m.tool_calls:
                            yield UnifiedStreamEvent(
                                event="message.agent_delta",
                                data={
                                    "message_id": str(assistant_message_id),
                                    "type": "tool_call",
                                    "tool_call_id": tc.get("id", ""),
                                    "tool_name": tc.get("name", ""),
                                    "tool_input": tc.get("args", {}),
                                },
                            )
                    # 某些 provider（如 Anthropic 兼容端点）可能主要通过 updates
                    # 返回最终 AIMessage；这里补充抽取正文，避免只看到空 completed。
                    if isinstance(m, AIMessage):
                        delta = _extract_text_content(m)
                        if delta:
                            if _should_dedupe_message(m):
                                message_key = _message_dedupe_key(m)
                                if message_key in emitted_deltas:
                                    continue
                                emitted_deltas.add(message_key)
                            final_content_parts.append(delta)
                            yield UnifiedStreamEvent(
                                event="message.delta",
                                data={
                                    "message_id": str(assistant_message_id),
                                    "delta": delta,
                                },
                            )
                    # todo 更新
                    if isinstance(m, dict) and m.get("type") == "todo":
                        yield UnifiedStreamEvent(
                            event="message.agent_delta",
                            data={
                                "message_id": str(assistant_message_id),
                                "type": "todo",
                                "content": m.get("content", ""),
                            },
                        )

    # 产出完成事件，携带累积的最终内容
    final_content = "".join(final_content_parts)
    yield UnifiedStreamEvent(
        event="message.completed",
        data={
            "content": final_content,
            "reasoning_content": "",
            "response_metadata": {},
        },
    )


def _extract_text_content(msg: Any) -> str:
    """从 LangChain 消息对象中提取文本内容。

    兼容 str 内容和 list[dict] 内容（多模态格式）。
    """
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                text_parts.append(block)
        return "".join(text_parts)
    return str(content) if content else ""


def _message_dedupe_key(msg: Any) -> str:
    msg_id = getattr(msg, "id", None)
    if isinstance(msg_id, str) and msg_id:
        return f"id:{msg_id}"
    return f"content:{_extract_text_content(msg)}"


def _should_dedupe_message(msg: Any) -> bool:
    msg_type = str(getattr(msg, "type", ""))
    return "chunk" not in msg_type.lower()


def _normalize_stream_event(event: Any) -> tuple[str, Any]:
    """兼容 LangGraph stream_mode 事件的两种格式。

    旧格式：
      - (message, metadata)
      - {updates...}
    新格式（指定 stream_mode 列表时）：
      - ("messages", (message, metadata))
      - ("updates", {updates...})
    """
    if isinstance(event, tuple) and len(event) == 2:
        tag, payload = event
        if tag in {"messages", "updates"}:
            return str(tag), payload
        # 兼容旧格式：直接把 tuple 视为 messages 负载
        return "messages", event
    if isinstance(event, dict):
        return "updates", event
    return "unknown", event

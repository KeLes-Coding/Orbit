"""Orbit Agent Runtime 请求与上下文定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class OrbitRuntimeRequest:
    """一次 runtime 调用的稳定输入对象。"""

    # 这些字段描述的是“一次 agent/chat 执行”的宿主侧事实，
    # 目的是让上层 service 不必理解 LangGraph / DeepAgents 的内部对象形状。
    conversation_id: str
    assistant_message_id: str
    stream_id: str
    thread_id: str
    chat_mode: str
    agent_type: str | None
    input_messages: list
    llm_config: Any
    model: str | None
    file_refs: list[dict[str, Any]] | None = None
    sandbox_scope: str = "run"
    sandbox_id: str | None = None
    artifact_policy: dict[str, Any] | None = None


@dataclass(frozen=True)
class OrbitRuntimeContext:
    """runtime 内部共享依赖。"""

    # request 负责传“本次执行是谁、为何而执行”，
    # 其余字段则是执行过程中需要复用的共享依赖。
    request: OrbitRuntimeRequest
    tool_runtime: Any
    stream_writer: Callable[[dict], None] | None = None

    def with_stream_writer(
        self,
        stream_writer: Callable[[dict], None] | None,
    ) -> "OrbitRuntimeContext":
        """返回绑定执行期 stream_writer 的新上下文。"""
        return OrbitRuntimeContext(
            request=self.request,
            tool_runtime=self.tool_runtime,
            stream_writer=stream_writer,
        )

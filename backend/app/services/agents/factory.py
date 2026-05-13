"""Agent 工厂：从 LLMConfig + system prompt 构建 deep agent。

核心职责：
  1. 根据 LLMConfig 获取对应的 LangChain chat model
  2. 组装 system prompt、memory、权限和 HITL 配置
  3. 通过 create_deep_agent() 返回编译好的 LangGraph StateGraph

deepagents 内置能力接入说明：
  - 内置工具: write_todos / ls / read_file / write_file / edit_file / glob / grep / execute / task
  - memory:      注入项目级 AGENTS.md 上下文，不替代 Conversation.summary
  - interrupt_on: 针对写操作开启 HITL 审批（write_file / edit_file / execute）
  - permissions:  限制文件系统工具的读写路径范围
"""
from dataclasses import replace
import os
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from app.models.llm_config import LLMConfig
from app.services.llm.providers.base import BaseLLMProvider, LLMRuntimeConfig
from app.services.llm.providers.registry import get_provider

# 单进程内共享的 MemorySaver，agent 对话状态在此持久化。
_shared_checkpointer = MemorySaver()

# 项目根目录下的 AGENTS.md 文件路径（如果存在）。
_PROJECT_AGENTS_MD = Path("/home/keles/WorkSpace/Orbit/AGENTS.md")
_DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
_DISABLED_BUILTIN_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "task",
    }
)


def _resolve_memory_sources() -> list[str] | None:
    """返回可用的 AGENTS.md 路径列表。文件不存在时返回 None。"""
    if _PROJECT_AGENTS_MD.exists():
        return [str(_PROJECT_AGENTS_MD)]
    return None


def _build_interrupt_on() -> dict:
    """构建 HITL 审批配置。

    写操作需要用户审批后才能执行，防止 agent 误操作：
      - write_file / edit_file: 允许全部决策类型（approve/edit/reject/respond）
      - execute（shell 命令）: 仅允许 approve/reject 二元决策
    """
    return {
        "write_file": True,
        "edit_file": True,
        "execute": {"allowed_decisions": ["approve", "reject"]},
    }


def _build_permissions() -> list:
    """构建文件系统权限规则。

    规则按声明顺序求值，先匹配先生效：
      1. 允许读取整个项目目录
      2. 允许在 /tmp/sandbox/ 下写入文件
    未匹配的操作默认允许。
    """
    from deepagents.middleware.filesystem import FilesystemPermission

    return [
        FilesystemPermission(operations=["read"], paths=["/"], mode="allow"),
        FilesystemPermission(operations=["write"], paths=["/tmp/sandbox/"], mode="allow"),
    ]


def _resolve_agent_model_provider(
    llm_config: LLMConfig,
    *,
    model: str | None = None,
) -> tuple[BaseLLMProvider, LLMRuntimeConfig]:
    """为 agent 选择实际 provider/runtime_config。

    DeepSeek V4 系列在 agent 多轮工具调用场景下优先走 Anthropic 兼容接口，
    避免 OpenAI-compatible + reasoning_content 回传链路在 deepagents 内部中断。
    """
    provider = get_provider(llm_config.provider)
    if provider is None:
        raise ValueError(f"不支持的模型供应商：{llm_config.provider}")

    runtime_config = provider.from_model_config(llm_config, model=model)
    if not _should_route_agent_to_anthropic(runtime_config):
        return provider, runtime_config

    anthropic_provider = get_provider("anthropic")
    if anthropic_provider is None:
        raise ValueError("Anthropic provider 未注册，无法为 DeepSeek V4 Pro 构建 agent")

    return anthropic_provider, replace(
        runtime_config,
        provider=anthropic_provider.provider,
        base_url=_to_anthropic_compatible_base_url(runtime_config.base_url),
    )


def _should_route_agent_to_anthropic(runtime_config: LLMRuntimeConfig) -> bool:
    provider = (runtime_config.provider or "").strip().lower()
    model = (runtime_config.model or "").strip().lower()
    # DeepSeek V4 系列（pro/flash）走 Anthropic 兼容端点最稳定。
    return provider == "deepseek" and model.startswith("deepseek-v4")


def _to_anthropic_compatible_base_url(base_url: str | None) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return _DEEPSEEK_ANTHROPIC_BASE_URL
    if normalized.endswith("/anthropic"):
        return normalized
    return f"{normalized}/anthropic"


def build_agent(
    llm_config: LLMConfig,
    *,
    system_prompt: str = "",
    model: str | None = None,
):
    """从 LLMConfig 构建 deep agent。

    不传自定义 tools——直接使用 deepagents 内置的 9 个工具
    （write_todos / ls / read_file / write_file / edit_file / glob / grep / execute / task），
    避免与内置工具重复。
    """
    provider, runtime_config = _resolve_agent_model_provider(
        llm_config,
        model=model,
    )
    _register_agent_harness_profile(runtime_config)
    chat_model = provider.build_chat_model(runtime_config)

    instructions = system_prompt or _default_system_prompt()

    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=chat_model,
        system_prompt=instructions,
        checkpointer=_shared_checkpointer,
        memory=_resolve_memory_sources(),
        interrupt_on=_build_interrupt_on(),
        permissions=_build_permissions(),
    )
    return agent


def _default_system_prompt() -> str:
    """默认 agent system prompt。

    强调"先探索再回答"的行为模式，引导模型主动使用工具获取上下文，
    而非直接依赖训练数据回答。
    """
    return """你是 Orbit 项目中的 AI Agent，拥有直接操作项目文件系统的能力。

## 核心行为准则

1. **永远先探索再回答** — 在给出任何关于项目代码的回答之前，先用工具确认事实。
   不要猜测文件路径、函数签名或实现细节，始终用 ls / read_file / grep 验证。

2. **使用 todo 列表管理复杂任务** — 面对多步骤任务时，先用 write_todos 规划步骤，
   完成一步后更新 todo 状态，让用户清楚当前进度。

3. **引用具体位置** — 回答中引用文件路径和行号，让用户知道你的信息来源。

4. **需要写入时主动说明** — 写入文件操作需要用户审批。先解释你打算做什么、为什么，
   然后等待审批通过后再执行。

## 你拥有的工具

| 工具 | 用途 |
|------|------|
| `ls` | 列出目录内容 |
| `read_file` | 读取文件 |
| `glob` | 按模式搜索文件 |
| `grep` | 在代码中搜索关键词 |
| `write_file` | 创建/覆盖文件（需审批） |
| `edit_file` | 编辑文件（需审批） |
| `execute` | 执行 shell 命令（需审批） |
| `write_todos` | 管理待办列表 |
| `task` | 派发子 agent 处理独立任务 |

## 回答风格

- 用中文回答
- 简洁、结构化，使用列表或表格组织信息
- 代码引用使用 `文件路径:行号` 格式"""


def _enable_builtin_tools() -> bool:
    value = os.getenv("ORBIT_AGENT_ENABLE_BUILTIN_TOOLS", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _register_agent_harness_profile(runtime_config: LLMRuntimeConfig) -> None:
    """注册 agent 运行时的工具集配置。

    当前 Web 对话阶段默认关闭 deepagents 内置文件/执行类工具，避免越权感知。
    若后续要恢复完整内置工具，可设置 ORBIT_AGENT_ENABLE_BUILTIN_TOOLS=true。
    """
    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        register_harness_profile,
    )

    model = (runtime_config.model or "").strip()
    profile_key = f"{runtime_config.provider}:{model}" if model else runtime_config.provider
    if _enable_builtin_tools():
        register_harness_profile(
            profile_key,
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=True),
            ),
        )
        return

    register_harness_profile(
        profile_key,
        HarnessProfile(
            excluded_tools=_DISABLED_BUILTIN_TOOLS,
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )

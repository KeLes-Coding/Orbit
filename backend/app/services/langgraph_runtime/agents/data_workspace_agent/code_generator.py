"""Data Workspace 的代码生成与修复 harness。"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import HumanMessage

from app.services.langgraph_runtime.agents.data_workspace_agent.code_cleaner import DataCodeCleaner
from app.services.langgraph_runtime.agents.data_workspace_agent.runtime_contract import DataRuntimeContract
from app.services.langgraph_runtime.core.agent_contract import LlmInvoker
from app.services.langgraph_runtime.sandbox import SandboxInputFile


class DataCodeGenerator:
    """封装 LLM codegen / repair，保证两者共用同一份运行时契约。"""

    def __init__(
        self,
        *,
        llm_invoke: LlmInvoker | None,
        cleaner: DataCodeCleaner,
        contract: DataRuntimeContract,
    ) -> None:
        self._llm_invoke = llm_invoke
        self._cleaner = cleaner
        self._contract = contract

    async def generate(
        self,
        *,
        user_query: str,
        input_files: list[SandboxInputFile],
        fallback_code: str,
        token_usage_sink: Callable[[dict], None],
    ) -> str:
        """生成第一版 analysis.py；无 LLM 时使用确定性兜底脚本。"""

        if self._llm_invoke is None:
            return fallback_code
        text = await self._collect_llm_text(
            prompt=self._build_codegen_prompt(user_query=user_query, input_files=input_files),
            token_usage_sink=token_usage_sink,
        )
        return self._cleaner.clean(text) or fallback_code

    async def repair(
        self,
        *,
        user_query: str,
        input_files: list[SandboxInputFile],
        previous_code: str,
        stdout: str,
        stderr: str,
        validation_error: str | None,
        token_usage_sink: Callable[[dict], None],
    ) -> str:
        """基于预检/执行/manifest 错误修复完整 analysis.py。"""

        if self._llm_invoke is None:
            return previous_code
        text = await self._collect_llm_text(
            prompt=self._build_repair_prompt(
                user_query=user_query,
                input_files=input_files,
                previous_code=previous_code,
                stdout=stdout,
                stderr=stderr,
                validation_error=validation_error,
            ),
            token_usage_sink=token_usage_sink,
        )
        return self._cleaner.clean(text) or previous_code

    async def _collect_llm_text(
        self,
        *,
        prompt: str,
        token_usage_sink: Callable[[dict], None],
    ) -> str:
        if self._llm_invoke is None:
            return ""
        parts: list[str] = []
        async for chunk in self._llm_invoke(
            [HumanMessage(content=prompt)],
            None,
            False,
            None,
            None,
            None,
        ):
            if chunk.content_delta:
                parts.append(chunk.content_delta)
            if chunk.token_usage:
                token_usage_sink(chunk.token_usage)
        return "".join(parts)

    def _build_codegen_prompt(
        self,
        *,
        user_query: str,
        input_files: list[SandboxInputFile],
    ) -> str:
        file_lines = self._format_input_files(input_files)
        return (
            "你是 Orbit Data Workspace Agent。请只生成一个 Python 脚本，用于写入 "
            "/workspace/work/analysis.py。\n"
            f"{self._contract.render_prompt_contract()}\n\n"
            f"用户问题：{user_query}\n\n"
            f"输入文件：\n{file_lines}\n"
        )

    def _build_repair_prompt(
        self,
        *,
        user_query: str,
        input_files: list[SandboxInputFile],
        previous_code: str,
        stdout: str,
        stderr: str,
        validation_error: str | None,
    ) -> str:
        file_lines = self._format_input_files(input_files)
        return (
            "请修复 Orbit Data Workspace 的 /workspace/work/analysis.py。"
            "只输出修复后的完整 Python 代码。\n\n"
            "修复必须遵守以下同一份运行时契约，不能发明新的 helper API：\n\n"
            f"{self._contract.render_prompt_contract()}\n\n"
            f"用户问题：{user_query}\n\n"
            f"输入文件：\n{file_lines}\n\n"
            f"上一次 stdout：\n{stdout or '(empty)'}\n\n"
            f"上一次 stderr：\n{stderr or '(empty)'}\n\n"
            f"预检或 manifest 校验错误：\n{validation_error or '(none)'}\n\n"
            f"上一版代码：\n```python\n{previous_code}\n```"
        )

    @staticmethod
    def _format_input_files(input_files: list[SandboxInputFile]) -> str:
        return "\n".join(f"- {item.name} ({len(item.content)} bytes)" for item in input_files)

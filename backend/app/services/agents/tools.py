"""Agent 只读工具集（MVP 阶段）。

首批工具仅开放安全的只读操作：
  - list_files: 列出目录内容
  - read_file:  读取文件内容
  - search_code: 在项目中搜索代码

所有路径操作使用 _safe_path() 限制在项目根目录内，防止目录遍历攻击。
"""
import subprocess
from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

# 项目根目录，所有工具操作的边界。
_PROJECT_ROOT = Path("/home/keles/WorkSpace/Orbit")


def _safe_path(relative_path: str) -> Path:
    """将用户提供的相对路径规范化为项目根目录下的绝对路径。

    解析后检查是否仍在项目根内，防止 ../ 逃逸攻击。
    """
    # 空路径或根路径默认指向项目根
    cleaned = relative_path.strip() or "."
    raw = (_PROJECT_ROOT / cleaned).resolve()
    # resolve 后再比较，确保符号链接和相对路径都被标准化
    if not raw.is_relative_to(_PROJECT_ROOT.resolve()):
        raise ValueError(f"路径超出项目范围：{relative_path}")
    return raw


@tool
def list_files(
    directory: Annotated[str, "要列出的目录路径，相对于项目根目录"] = ".",
) -> str:
    """列出指定目录下的文件和子目录（非递归）。"""
    try:
        target = _safe_path(directory)
        if not target.exists():
            return f"目录不存在：{directory}"
        if not target.is_dir():
            return f"不是目录：{directory}"
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for item in items:
            suffix = "/" if item.is_dir() else ""
            lines.append(f"  {item.name}{suffix}")
        if not lines:
            return f"目录为空：{directory}"
        return "\n".join(lines)
    except ValueError as e:
        return str(e)
    except PermissionError:
        return f"没有权限访问目录：{directory}"


@tool
def read_file(
    file_path: Annotated[str, "要读取的文件路径，相对于项目根目录"],
) -> str:
    """读取指定文件的前 2000 行内容。"""
    try:
        target = _safe_path(file_path)
        if not target.exists():
            return f"文件不存在：{file_path}"
        if not target.is_file():
            return f"不是文件：{file_path}"
        content = target.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        max_lines = 2000
        if len(lines) > max_lines:
            truncated = "\n".join(lines[:max_lines])
            return f"{truncated}\n\n...（已截断，共 {len(lines)} 行，仅显示前 {max_lines} 行）"
        return content
    except ValueError as e:
        return str(e)
    except PermissionError:
        return f"没有权限读取文件：{file_path}"
    except UnicodeDecodeError:
        return f"无法以 UTF-8 编码读取文件（可能是二进制文件）：{file_path}"


@tool
def search_code(
    query: Annotated[str, "搜索关键词或正则表达式"],
    path: Annotated[str, "搜索路径，相对于项目根目录，默认为项目根"] = ".",
) -> str:
    """在项目代码中搜索匹配的行（使用 grep -rn）。

    自动排除 .git、node_modules、__pycache__、.venv 等常见非源码目录。
    """
    try:
        target = _safe_path(path)
        if not target.exists():
            return f"路径不存在：{path}"
        # 排除常见的非源码目录
        exclude_dirs = [
            "--exclude-dir=.git",
            "--exclude-dir=node_modules",
            "--exclude-dir=__pycache__",
            "--exclude-dir=.venv",
            "--exclude-dir=venv",
            "--exclude-dir=Orbit",  # Python venv 目录
            "--exclude-dir=.mypy_cache",
            "--exclude-dir=.pytest_cache",
            "--exclude-dir=.ruff_cache",
        ]
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.ts", "--include=*.tsx",
             "--include=*.js", "--include=*.json", "--include=*.md", "--include=*.yml",
             "--include=*.yaml", "--include=*.toml", "--include=*.cfg"]
            + exclude_dirs
            + [query, str(target)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout.strip()
        if not output:
            return f"未找到匹配 \"{query}\" 的结果"
        lines = output.splitlines()
        max_lines = 100
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n\n...（结果过多，已截断，共 {len(lines)} 条）"
        return output
    except ValueError as e:
        return str(e)
    except subprocess.TimeoutExpired:
        return f"搜索超时：{query}"
    except FileNotFoundError:
        return "grep 命令不可用"


# 首批只读工具列表
READ_ONLY_TOOLS = [list_files, read_file, search_code]

"""Data Workspace 生成代码的确定性清洗。"""

from __future__ import annotations


class DataCodeCleaner:
    """只做确定性清洗，不替模型改写业务逻辑。"""

    def clean(self, text: str) -> str:
        """提取 Python 代码块并去除首尾空白。"""

        stripped = text.strip()
        if "```" not in stripped:
            return stripped
        parts = stripped.split("```")
        for index, part in enumerate(parts):
            if index % 2 == 0:
                continue
            block = part.strip()
            if block.startswith("python"):
                return block.removeprefix("python").strip()
        return parts[1].strip()

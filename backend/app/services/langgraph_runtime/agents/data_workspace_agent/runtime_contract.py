"""Data Workspace 生成代码时使用的运行时契约。"""

from __future__ import annotations


class DataRuntimeContract:
    """集中维护 sandbox 内暴露给模型的 helper API 与产物协议。"""

    def render_prompt_contract(self) -> str:
        """渲染给 codegen / repair 共用的提示词片段。"""

        return (
            "Orbit Data Workspace runtime contract：\n"
            "1. 只能生成一个完整 Python 脚本，脚本最终写入 /workspace/work/analysis.py。\n"
            "2. 必须通过 sys.path.insert(0, 'runtime') 引入 sandbox runtime bundle。\n"
            "3. 只能读取 input/、runtime/、work/，只能写 output/。\n"
            "4. sandbox 只保证 Python 标准库和 orbit_data helper；禁止 pandas/numpy/polars/openpyxl。\n"
            "5. load_table(path) 返回 list[dict[str, str]]，不是 pandas DataFrame；不要使用 .columns/.groupby/.to_csv。\n"
            "6. 必须写出 output/artifact_manifest.json，最终产物只通过 manifest 声明。\n"
            "7. 至少产出 report 和 code 两类 artifact。\n\n"
            "可用 helper import：\n"
            "from orbit_data.loaders import load_table, load_json, load_excel_sheets\n"
            "from orbit_data.profiler import profile_table\n"
            "from orbit_data.artifacts import save_json_artifact, save_text_artifact, write_artifact_manifest\n\n"
            "helper 签名，必须严格遵守：\n"
            "load_table(path: str | Path, *, max_rows: int | None = None) -> list[dict[str, str]]\n"
            "load_json(path: str | Path) -> Any\n"
            "load_excel_sheets(path: str | Path, *, max_rows: int | None = None) -> dict[str, list[list[str]]]\n"
            "profile_table(rows: list[dict[str, str]]) -> dict[str, Any]\n"
            "save_json_artifact(path: str, data: Any) -> dict[str, Any]\n"
            "save_text_artifact(path: str, text: str) -> dict[str, Any]\n"
            "write_artifact_manifest(artifacts: list[dict[str, Any]]) -> dict[str, Any]\n\n"
            "禁止的错误调用：\n"
            "save_text_artifact(name='report', content=text, group='report')\n"
            "save_text_artifact('report', text, 'report')\n"
            "save_json_artifact(name='table', data=rows)\n\n"
            "manifest item schema：\n"
            "{'type': 'table'|'chart'|'code'|'text'|'report'|'json', 'name': str, 'path': str, 'preview': optional str, 'metadata': optional dict}\n\n"
            "最小骨架示例：\n"
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, 'runtime')\n"
            "from orbit_data.artifacts import save_json_artifact, save_text_artifact, write_artifact_manifest\n"
            "from orbit_data.loaders import load_table\n"
            "rows = load_table(Path('input/example.csv'))\n"
            "save_json_artifact('result.json', {'rows': rows[:20]})\n"
            "save_text_artifact('report.md', '# Report\\n')\n"
            "save_text_artifact('analysis.py', Path('work/analysis.py').read_text(encoding='utf-8'))\n"
            "write_artifact_manifest([\n"
            "  {'type': 'json', 'name': 'result', 'path': 'result.json'},\n"
            "  {'type': 'report', 'name': 'report', 'path': 'report.md'},\n"
            "  {'type': 'code', 'name': 'analysis_code', 'path': 'analysis.py'},\n"
            "])"
        )

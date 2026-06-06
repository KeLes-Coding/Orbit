"""sandbox 脚本使用的数据加载 helper。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile
from xml.etree import ElementTree


def load_table(path: str | Path, *, max_rows: int | None = None) -> list[dict[str, str]]:
    """将 CSV/TSV 读取为 dict 行列表。"""

    file_path = Path(path)
    delimiter = "\t" if file_path.suffix.lower() == ".tsv" else ","
    rows: list[dict[str, str]] = []
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for index, row in enumerate(reader):
            if max_rows is not None and index >= max_rows:
                break
            rows.append({str(key): value for key, value in row.items()})
    return rows


def load_json(path: str | Path) -> Any:
    """读取 JSON 文件。"""

    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_excel_sheets(path: str | Path, *, max_rows: int | None = None) -> dict[str, list[list[str]]]:
    """只用标准库读取 .xlsx workbook 的基础单元格值。"""

    workbook_path = Path(path)
    namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(workbook_path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("a:si", namespace):
                text_parts = [node.text or "" for node in item.findall(".//a:t", namespace)]
                shared_strings.append("".join(text_parts))

        sheets: dict[str, list[list[str]]] = {}
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/sheet") or not name.endswith(".xml"):
                continue
            root = ElementTree.fromstring(archive.read(name))
            rows: list[list[str]] = []
            for index, row_node in enumerate(root.findall(".//a:sheetData/a:row", namespace)):
                if max_rows is not None and index >= max_rows:
                    break
                row_values: list[str] = []
                for cell in row_node.findall("a:c", namespace):
                    value_node = cell.find("a:v", namespace)
                    raw_value = value_node.text if value_node is not None else ""
                    if cell.attrib.get("t") == "s" and raw_value:
                        raw_value = shared_strings[int(raw_value)]
                    row_values.append(raw_value or "")
                rows.append(row_values)
            sheets[Path(name).stem] = rows
        return sheets

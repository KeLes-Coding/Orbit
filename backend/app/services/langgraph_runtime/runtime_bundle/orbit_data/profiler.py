"""轻量表格 profile helper。"""

from __future__ import annotations

from collections import Counter
from typing import Any


def profile_table(rows: list[dict[str, Any]], *, sample_size: int = 5) -> dict[str, Any]:
    """为 dict 行列表生成紧凑 profile。"""

    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    null_counts: dict[str, int] = {column: 0 for column in columns}
    value_examples: dict[str, list[str]] = {column: [] for column in columns}
    for row in rows:
        for column in columns:
            value = row.get(column)
            if value in (None, ""):
                null_counts[column] += 1
                continue
            examples = value_examples[column]
            text = str(value)
            if text not in examples and len(examples) < sample_size:
                examples.append(text)

    return {
        "row_count": len(rows),
        "columns": columns,
        "null_counts": null_counts,
        "examples": value_examples,
    }


def top_values(rows: list[dict[str, Any]], column: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """返回指定列中最常见的值。"""

    counter = Counter(str(row.get(column, "")) for row in rows if row.get(column, "") != "")
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]

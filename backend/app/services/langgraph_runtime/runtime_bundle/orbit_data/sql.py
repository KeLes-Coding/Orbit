"""面向 dict 行列表的极简查询 helper。"""

from __future__ import annotations

from typing import Any, Callable


def filter_rows(
    rows: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    """返回满足 predicate 的行。"""

    return [row for row in rows if predicate(row)]

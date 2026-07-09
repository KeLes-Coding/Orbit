"""图表 spec helper。"""

from __future__ import annotations

from typing import Any


def make_bar_chart_spec(
    *,
    title: str,
    categories: list[str],
    values: list[int | float],
    series_name: str = "value",
) -> dict[str, Any]:
    """返回一个紧凑的 ECharts 兼容柱状图 option。"""

    return {
        "title": {"text": title},
        "tooltip": {},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": [{"name": series_name, "type": "bar", "data": values}],
    }

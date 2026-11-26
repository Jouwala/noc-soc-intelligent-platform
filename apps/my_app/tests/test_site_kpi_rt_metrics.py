# -*- coding: utf-8 -*-
"""站点 KPI 实时查询：返回给前端的指标清单单测（不连 UME、不连库）。

2026-11-25 复审发现：4G 的「单用户速率」由吞吐 / 活跃用户数现算后写进 `series`，
但没登记进 `result.metrics`；前端 `SiteKpiRt.tsx` 按 `result.metrics` 过滤要画的
曲线，于是默认勾选的这条曲线**永远不渲染**，页面上静默少一条线。
这里锁住「4G 派生指标必须出现在清单里、5G 不要重复」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.ran import ran_site_kpi_rt_service as svc  # noqa: E402


def _keys(tech: str, metrics_def) -> list[str]:
    return [m["key"] for m in svc.describe_metrics(tech, metrics_def)]


def test_4g_metrics_include_derived_user_throughput():
    keys = _keys("4G", svc.METRICS_4G)
    assert "dl_user_thr_mbps" in keys, "4G 下行单用户速率必须出现在 metrics 里"
    assert "ul_user_thr_mbps" in keys, "4G 上行单用户速率必须出现在 metrics 里"
    # 派生指标同样要带中文标签与单位，前端直接展示
    by_key = {m["key"]: m for m in svc.describe_metrics("4G", svc.METRICS_4G)}
    assert by_key["dl_user_thr_mbps"]["label"] == "下行单用户速率"
    assert by_key["dl_user_thr_mbps"]["unit"] == "Mbps"


def test_5g_metrics_do_not_duplicate_derived_keys():
    """5G 模板本身就有单用户速率（原生指标），不能再追加派生项。"""
    keys = _keys("5G", svc.METRICS_5G)
    assert keys.count("dl_user_thr_mbps") == 1
    assert keys.count("ul_user_thr_mbps") == 1


def test_metric_list_matches_series_length():
    """清单条数 == 每个小区的曲线条数（少一条就是页面上少一列）。"""
    assert len(svc.describe_metrics("4G", svc.METRICS_4G)) == len(svc.METRICS_4G) + len(svc.DERIVED_4G)
    assert len(svc.describe_metrics("5G", svc.METRICS_5G)) == len(svc.METRICS_5G)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

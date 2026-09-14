#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""漏斗统计（MVP，2026-09-12）

移植自 career-ops-hq/career-ops 的 `funnel-velocity.mjs`（MIT）里的
**统计诚实规则（council 审过，不可谈）**：

  1. **中位数必须报右删失**（"n 个仍在等待，已排除"）。
     实测背景：61% 的投递会被 ghost，只算"已完成"的样本是**幸存者偏差**——
     越慢的公司越可能还在进行中，把它们剔掉会系统性低估等待时间。
  2. **0 天跳变（同日补录）排除在中位数外，但仍计数**。
  3. **n < 20 不许给任何"比较倍数"结论**。
  4. **超出基准范围时必须带选择偏差提示**。
  5. **每个市场基准必须带年份 + "方向性"字样**（基准是参考不是承诺）。

本模块只做统计，不读平台、不写库。
"""
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence

MIN_N_FOR_COMPARISON = 20      # 少于此数不下"倍数"结论


def median_with_censoring(values: Sequence[float], censored_count: int = 0,
                          label: str = "") -> dict:
    """带右删失报告的中位数。

    values         —— 已观测到终点的样本（如已收到回复的天数）
    censored_count —— 仍在等待（右删失）的样本数，**不计入中位数但必须报出来**
    """
    vals = sorted(float(v) for v in values)
    n = len(vals)
    med = median(vals) if vals else None
    note = ""
    if n == 0:
        note = "无已完结样本，无法给中位数"
    elif censored_count > 0:
        pct = censored_count / (n + censored_count) * 100
        note = (f"右删失：{censored_count} 个仍在等待（占 {pct:.0f}%）已排除——"
                f"只算已完结样本会低估等待时间（幸存者偏差）")
    return {
        "label": label,
        "median": med,
        "n": n,
        "censored": censored_count,
        "note": note,
    }


def stage_velocity(transitions: Iterable[dict]) -> dict:
    """按阶段跳变算中位/p75 天数。

    transitions: [{'from': 'Applied', 'to': 'Responded', 'days': 3}, ...]
    0 天跳变（同日补录）排除在中位数外但仍计数。
    """
    by_hop: Dict[str, List[float]] = {}
    zero_hop: Dict[str, int] = {}
    for t in transitions:
        hop = f"{t.get('from', '?')} → {t.get('to', '?')}"
        d = float(t.get("days") or 0)
        if d <= 0:
            zero_hop[hop] = zero_hop.get(hop, 0) + 1
            continue          # ★ 排除但不丢：单独计数
        by_hop.setdefault(hop, []).append(d)

    out = {}
    for hop, ds in by_hop.items():
        ds_sorted = sorted(ds)
        p75 = ds_sorted[min(len(ds_sorted) - 1, int(round(0.75 * (len(ds_sorted) - 1))))]
        out[hop] = {
            "n": len(ds),
            "median_days": median(ds_sorted),
            "p75_days": p75,
            "excluded_zero_day": zero_hop.get(hop, 0),
            "note": (f"已排除 {zero_hop.get(hop, 0)} 条 0 天跳变（同日补录），仍计数"
                     if zero_hop.get(hop) else ""),
        }
    for hop, cnt in zero_hop.items():
        if hop not in out:
            out[hop] = {"n": 0, "median_days": None, "p75_days": None,
                        "excluded_zero_day": cnt, "note": "样本全部为 0 天跳变，无法给中位数"}
    return out


def calibrate(own_rate: Optional[float], own_n: int,
              benchmark: Optional[dict] = None) -> dict:
    """把自己的漏斗转化率与市场基准对照。**遵守全部统计诚实规则。**

    benchmark: {'low': 0.05, 'high': 0.10, 'year': 2026, 'source': '...'}
    """
    res = {"own_rate": own_rate, "own_n": own_n, "notes": []}

    if own_n < MIN_N_FOR_COMPARISON:
        res["notes"].append(
            f"⚠️ 样本量 n={own_n} < {MIN_N_FOR_COMPARISON}，**不给任何倍数/比较结论**"
            f"（小样本的比率波动可以很大）")
        res["comparable"] = False
    else:
        res["comparable"] = True

    if not benchmark:
        res["notes"].append("无基准可比")
        return res

    yr = benchmark.get("year")
    res["benchmark"] = benchmark
    res["notes"].append(
        f"基准口径：{benchmark.get('low')}-{benchmark.get('high')}，"
        f"**{yr} 年数据，仅作方向性参考**（不是承诺，也不要当目标线）")

    if own_rate is None:
        res["notes"].append("自身转化率未知，无法对照")
        return res

    lo, hi = benchmark.get("low"), benchmark.get("high")
    if lo is not None and hi is not None:
        if own_rate > hi:
            res["position"] = "above_range"
            # ★ 超出范围必须带选择偏差提示
            res["notes"].append(
                "⚠️ 高于基准区间：**注意选择偏差**——投递渠道/岗位类型/地区差异都可能解释，"
                "不能直接读成\"我比市场强\"")
        elif own_rate < lo:
            res["position"] = "below_range"
            res["notes"].append("低于基准区间：先看样本量是否足够（见上），再看漏斗哪一段在漏")
        else:
            res["position"] = "in_range"
            res["notes"].append("落在基准区间内")
    return res


def funnel_rates(counts: Dict[str, int], stages: Sequence[str]) -> Dict[str, float]:
    """把各阶段计数折成"曾经到达该阶段"的比率（canonical ever* 口径）。

    counts 为各阶段的**累计到达数**（同一行即使后来被拒，也算到达过面试）。
    """
    out = {}
    base = counts.get(stages[0], 0) if stages else 0
    for st in stages:
        out[st] = (counts.get(st, 0) / base) if base else 0.0
    return out

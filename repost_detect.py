#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改标题重发检测（MVP，2026-09-12）

移植自 career-ops-hq/career-ops 的 `detect-reposts.mjs`（MIT）。

── 它要抓什么 ──
同一家公司、同一个岗位，用不同 URL 在 90 天内反复出现 → 几乎可以确定是
雇主把同一条 opening 反复挂出来，用来识别"僵而不死"的管道和幽灵岗。

── 两个约束把「重发」和「并行兄弟岗」分开（都是它误报三家从未重发的公司后加的）──

1. **最小跨度**：`first_seen` 是**扫描器**第一次看到该 URL 的时间，
   **不是雇主发布时间**。一次扫描会把整家公司一起扫掉 → 两条 URL 共享同一个
   first_seen 日期 = 它们是**并发**发布的，这正是重发的反面（重发要求该岗
   **后来重新出现**）。所以成员全部来自同一次扫描的簇跨度为 0 天，
   **根本不能作为重发证据**。（MIN_REPOST_SPAN_DAYS）

2. **标题"同一性"而非"相似度"**：不要用模糊匹配（如 0.6 Jaccard）。
   分区域/分细分的同一个岗恰好卡在模糊边界上——
   `Commercial Solutions Engineer - Munich` vs `- Berlin` 共享 5 个词里的 3 个
   → 一家"把一个岗扇到多个城市"的公司会被读成"在重发"。那些是
   **招不同 headcount 的不同 requisition，合并它们等于制造虚假信号**。
   本模块要求**标题词集合完全相同**：仍容忍词序与标点变化，
   但**绝不合并**只在城市/国家/语言/细分/职级词上不同的标题。

3. **聚合站无解**：多雇主聚合站的看板挂着许多不同雇主的岗，同名标题是
   **真正不同的岗**——上面所有规则的前提都不成立。实测在真实历史里，
   聚合站的簇与合法簇**形状上无法区分**，只能按配置跳过（aggregator 名单）。
"""
import re
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

# 允许被识别为同一条岗位的状态；其余（过期/无效/被拦）描述的是死岗，不是重发
REPOST_ELIGIBLE_STATUS = ("added",)

MIN_REPOST_SPAN_DAYS = 7      # 一个簇至少跨这么多天，才可能是"后来重新出现"
WINDOW_DAYS = 90              # 观察窗口
_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")


def normalize_title(title: str) -> str:
    return _PUNCT.sub(" ", (title or "").lower()).strip()


def title_identity_key(title: str) -> frozenset:
    """标题"同一性"键：归一化后的**词集合**（不看词序、不看标点）。

    刻意不做模糊匹配——只在城市/国家/语言/细分/职级词上不同的标题，
    词集合不同 → 不会被合并（这是设计意图，不是缺陷）。
    """
    return frozenset(w for w in normalize_title(title).split() if w)


def _as_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def find_reposts(rows: Iterable[dict], min_span_days: int = MIN_REPOST_SPAN_DAYS,
                 window_days: int = WINDOW_DAYS,
                 aggregators: Iterable[str] = (),
                 eligible_statuses: Iterable[str] = REPOST_ELIGIBLE_STATUS) -> List[dict]:
    """找出重发簇。

    每行需含：company / title / url / first_seen / status（可选 created_at）。
    eligible_statuses：只有这些状态的行参与判定（默认 career-ops 的 added；
    job-hunter 库里应传 ('uncertain','applied','verified')，其余是死岗/被拦，不是重发）。
    返回 [{company, title_key, titles, urls, days, span_days, count}, ...]
    """
    agg = {str(a).strip().lower() for a in aggregators if a}
    ok_status = {str(s).strip().lower() for s in eligible_statuses}
    groups: Dict[tuple, List[dict]] = {}
    for r in rows:
        if not isinstance(r, dict):
            try:
                r = dict(r)            # 兼容 sqlite3.Row 之类的映射类型
            except Exception:
                continue
        if str(r.get("status", "")).lower() not in ok_status:
            continue
        company = str(r.get("company") or "").strip().lower()
        if not company or company in agg:
            continue           # 聚合站：同名标题是不同雇主的岗，规则前提不成立
        key = title_identity_key(r.get("title") or "")
        if not key:
            continue
        groups.setdefault((company, key), []).append(r)

    out = []
    for (company, key), items in groups.items():
        urls = {str(i.get("url") or "") for i in items if i.get("url")}
        dates = {d for d in (_as_date(i.get("first_seen") or i.get("created_at")) for i in items) if d}
        if len(urls) < 2 or len(dates) < 2:
            continue           # 要么只有一个 URL，要么全来自同一次扫描 → 不是重发
        span = (max(dates) - min(dates)).days
        if span < min_span_days:
            continue           # ★ 最小跨度约束：跨度不足不能作为重发证据
        if span > window_days:
            continue
        out.append({
            "company": company,
            "title_key": sorted(key),
            "titles": sorted({str(i.get("title") or "") for i in items}),
            "urls": sorted(urls),
            "days": sorted(d.isoformat() for d in dates),
            "span_days": span,
            "count": len(urls),
        })
    out.sort(key=lambda x: (-x["count"], -x["span_days"]))
    return out


def explain(cluster: dict) -> str:
    return (f"  {cluster['company']}：{cluster['titles'][0][:40]} "
            f"→ {cluster['count']} 个 URL / 跨 {cluster['span_days']} 天"
            f"（{cluster['days'][0]} ~ {cluster['days'][-1]}）")

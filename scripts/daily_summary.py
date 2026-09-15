#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日收工摘要 → 推微信（2026-09-15 新增）

── 解决什么 ──
在此之前，一天投了多少、跳了多少、有多少失败，只写在 data/logs/run_*.log 里，
你不主动翻就等于没发生。这个脚本把当天结果推到你微信上一次，让「跑没跑、投成没投成」
不再靠翻日志。

指标口径（全部读 ab_experiment.db，单一事实源）：
  - 投递成功：status='APPLIED'（含 51job/liepin 的按钮回执确认）
  - 未验证：status='UNCERTAIN'
  - 失败：status='FAILED'
  - 跳过：status='SKIPPED'
  - 按平台拆分，并列出该平台最近一条失败的原因（若当天有失败）

用法：
    python3 scripts/daily_summary.py            # 推今天的
    python3 scripts/daily_summary.py --dry-run  # 只打印不推送
    python3 scripts/daily_summary.py --date 2026-09-14
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from notify import alert  # noqa: E402

DB = BASE / "ab_experiment.db"

STATUS_CN = {
    "APPLIED": "成功",
    "UNCERTAIN": "待验证",
    "FAILED": "失败",
    "SKIPPED": "跳过",
    "VERIFIED": "已验证",
}


def build_summary(day: str) -> tuple:
    """返回 (标题, 正文, 总数)。"""
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT platform, status, COUNT(*) AS n
           FROM applications_v2
           WHERE date(created_at) = ?
           GROUP BY platform, status""",
        (day,),
    ).fetchall()
    fails = conn.execute(
        """SELECT platform, company, title, reason FROM applications_v2
           WHERE date(created_at) = ? AND status = 'FAILED'
           ORDER BY created_at DESC LIMIT 3""",
        (day,),
    ).fetchall()
    conn.close()

    if not rows:
        return (f"{day} 投递摘要：今天没有记录",
                "定时任务可能没跑（Chrome 没起 / 被暂停锁拦住），值得看一眼。", 0)

    applied = sum(r["n"] for r in rows if r["status"] == "APPLIED")
    uncertain = sum(r["n"] for r in rows if r["status"] == "UNCERTAIN")
    failed = sum(r["n"] for r in rows if r["status"] == "FAILED")
    skipped = sum(r["n"] for r in rows if r["status"] == "SKIPPED")
    total = applied + uncertain + failed

    lines = [f"投出 **{total}** 份（成功 {applied} · 待验证 {uncertain} · 失败 {failed}）",
             f"规则跳过 {skipped}"]

    by_platform = {}
    for r in rows:
        d = by_platform.setdefault(r["platform"], {})
        d[r["status"]] = d.get(r["status"], 0) + r["n"]
    for p, d in sorted(by_platform.items()):
        parts = [f"{STATUS_CN.get(k, k)} {v}" for k, v in sorted(d.items()) if k != "SKIPPED"]
        if parts:
            lines.append(f"· {p}：" + " / ".join(parts))

    if fails:
        lines.append("")
        lines.append("最近失败原因：")
        for f in fails:
            lines.append(f"· [{f['platform']}] {(f['title'] or '')[:24]} — {(f['reason'] or '')[:40]}")

    title = f"{day[5:]} 投递 {total} 份（成功 {applied}）"
    return title, "\n".join(lines), total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    day = args.date
    try:
        # 校验日期格式，避免把垃圾参数传进 SQL
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        print(f"❌ 日期格式应为 YYYY-MM-DD：{day}")
        return 2

    title, body, total = build_summary(day)

    if args.dry_run:
        print(f"【{title}】\n{body}")
        return 0

    level = "info" if total > 0 else "warn"
    ok = alert(f"daily_summary:{day}", title, body, level=level, throttle=0)
    print(f"{'已推送' if ok else '未推送（见 data/logs/alerts.log）'}：{title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

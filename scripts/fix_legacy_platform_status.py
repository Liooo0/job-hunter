#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史数据订正：51job / 猎聘 被误标为 UNCERTAIN 的成功投递 → APPLIED

── 为什么 ──
platform_51job.py / platform_liepin.py 里，**成功分支**（按钮回执已变成「已申请/
已投递」、且已 applied += 1）把 status 硬编码成 "UNCERTAIN"、verified=0；
而真正没投出去的那条路只 print 不落库。结果 384 条真实成功的 51job 投递在库里
全是「不确定」，复盘结论变成「51job 从未验证过」。

证据（2026-09-15 核过）：
  1. 初版代码（commit 7b0f49a）就有守卫 `if "已申请" in state or "已投递" in state:`
     且写死 UNCERTAIN —— 这两件事在同一个 if 里；
  2. 这 384 条关联的 events 全是 type='apply' 且 error 为空的记录（失败路径不落库，
     所以「有记录」本身就等于「当时确认过按钮回执」）；
  3. decision='ALLOW' 是这条成功路径独有的写法（失败路径当时不写库）。

判定条件（两个都要满足）：
  platform IN ('51job','liepin') AND decision='ALLOW' AND status='UNCERTAIN'
  → 订正为 status='APPLIED', verified=1

安全性：
  - 只动 status / verified / updated_at 三个字段，**不动 decision** —— 所以熔断闸门
    （count_applied_since 认 applied/uncertain/ALLOW）与同公司去重（认 applied/
    uncertain）的口径完全不变；
  - 默认 dry-run，必须显式 --apply 才写；
  - 写之前把每条的旧值落盘到 revert 文件，可原样回滚；
  - 幂等：跑第二遍时已经没有满足条件的行（status 已不是 UNCERTAIN）。

用法：
    python3 scripts/fix_legacy_platform_status.py            # 预演，不写库
    python3 scripts/fix_legacy_platform_status.py --apply    # 真正执行
    python3 scripts/fix_legacy_platform_status.py --revert <revert.json>   # 回滚
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "ab_experiment.db"
REVERT_DIR = BASE / "backup-20260915"

SELECT_SQL = """
    SELECT application_id, platform, company, title, status, verified, decision, updated_at
    FROM applications_v2
    WHERE platform IN ('51job', 'liepin')
      AND decision = 'ALLOW'
      AND status = 'UNCERTAIN'
"""


def fetch_targets(conn) -> list:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(SELECT_SQL).fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只预演）")
    ap.add_argument("--revert", metavar="REVERT_JSON", help="按备份文件回滚")
    args = ap.parse_args()

    if not DB.exists():
        print(f"❌ 找不到数据库：{DB}")
        return 2
    conn = sqlite3.connect(str(DB))

    # ── 回滚模式 ──
    if args.revert:
        path = Path(args.revert)
        if not path.exists():
            print(f"❌ 找不到回滚文件：{path}")
            return 2
        rows = json.loads(path.read_text(encoding="utf-8"))
        for r in rows:
            conn.execute(
                "UPDATE applications_v2 SET status=?, verified=?, updated_at=? WHERE application_id=?",
                (r["status"], r["verified"], r["updated_at"], r["application_id"]),
            )
        conn.commit()
        print(f"✅ 已回滚 {len(rows)} 条到原值（status/verified/updated_at）")
        return 0

    targets = fetch_targets(conn)
    if not targets:
        print("✅ 没有需要订正的行（已订正过，或数据不符条件）—— 幂等，无需处理")
        return 0

    by_platform = {}
    for t in targets:
        by_platform[t["platform"]] = by_platform.get(t["platform"], 0) + 1
    print("待订正（status: UNCERTAIN → APPLIED, verified: 0 → 1）")
    for p, n in sorted(by_platform.items()):
        print(f"  {p}: {n} 条")
    print(f"  合计: {len(targets)} 条")
    for t in targets[:3]:
        print(f"    样例: {t['company'][:20]} | {t['title'][:24]} | {t['application_id']}")

    if not args.apply:
        print("\n这是预演（dry-run）。确认无误后加 --apply 真正写库。")
        return 0

    REVERT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    revert_path = REVERT_DIR / f"legacy_status_revert_{stamp}.json"
    revert_path.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")

    now = datetime.now().isoformat()
    conn.executemany(
        "UPDATE applications_v2 SET status='APPLIED', verified=1, updated_at=? WHERE application_id=?",
        [(now, t["application_id"]) for t in targets],
    )
    conn.commit()

    left = len(fetch_targets(conn))
    print(f"\n✅ 已订正 {len(targets)} 条；剩余待订正 {left} 条（应为 0）")
    print(f"   回滚文件：{revert_path}")
    print(f"   回滚命令：python3 scripts/fix_legacy_platform_status.py --revert {revert_path}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

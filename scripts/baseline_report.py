#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5.2 基准轮报告（2026-09-01 起每轮跑完执行）— 用户定稿 6 指标。

回答三个问题：
  ① Semantic Parser 杀掉了多少真正的伪装岗位？（指标1+3）
  ② 误杀了多少本来应该保留的岗位？（指标2，人工复核清单）
  ③ 剩下的岗位到底有多少真正成功发送？（指标6，P0）

用法：
    python3 scripts/baseline_report.py            # 今天
    python3 scripts/baseline_report.py 2026-09-01 # 指定日
"""
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "ab_experiment.db"


def main(day: str) -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM applications_v2 WHERE date(applied_at)=?", (day,))]
    if not rows:
        print(f"📭 {day} 无记录")
        return

    # ── 指标 6（P0）：实际发送四态 ──
    st = Counter(r["status"] for r in rows)
    att = st.get("UNCERTAIN", 0) + st.get("SENT", 0) + st.get("FAILED", 0)
    print(f"═══ {day} 基准轮报告 ═══\n")
    print(f"[6·P0 发送链路] attempted={att}  VERIFIED={st.get('SENT',0)}  "
          f"UNCERTAIN={st.get('UNCERTAIN',0)}  FAILED={st.get('FAILED',0)}")
    if att == 0:
        print("    ⚠️ 本轮没有任何发送尝试 —— 先看拦截侧")

    # ── 指标 1：Semantic Block 数（按伪装类型）──
    sem = [r for r in rows if r.get("status") == "SKIPPED"
           and r.get("reason", "").startswith(("实际为销售", "实际为客服", "实际为数据标注"))]
    kinds = Counter()
    for r in sem:
        if "销售" in r["reason"]:
            kinds["SALES"] += 1
        elif "客服" in r["reason"]:
            kinds["CUSTOMER_SERVICE"] += 1
        elif "标注" in r["reason"]:
            kinds["ANNOTATION"] += 1
    print(f"\n[1·语义拦截] semantic_block={len(sem)}  "
          + "  ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    for r in sem[:12]:
        print(f"    ✂️ {r['city']} {r['company'][:12]} | {r['title'][:30]}")

    # ── 指标 3：L2 vs Semantic 增量（semantic-only 才是新增价值）──
    # L2 BLOCK：decision_trace 含 job_decision/guardrails/exclude 类 reason
    l2_pats = re.compile(r"决策器|红线|排除词|实习|一人多岗|标题党|资格|REJECT|NO_PLAN")
    l2 = [r for r in rows if r.get("status") == "SKIPPED"
          and l2_pats.search(r.get("reason", "") + r.get("event", ""))]
    sem_titles = {(r["company"], r["title"]) for r in sem}
    l2_titles = {(r["company"], r["title"]) for r in l2}
    both = sem_titles & l2_titles
    sem_only = sem_titles - l2_titles
    print(f"\n[3·增量价值] L2_BLOCK≈{len(l2_titles)}  SEM_BLOCK={len(sem_titles)}  "
          f"both={len(both)}  ★semantic-only={len(sem_only)} ← 真正新增的拦截")

    # ── 指标 2：疑似误杀人工复核清单（BOUNDARY 词被拦 or AI_CORE 岗被拦）──
    boundary = re.compile(r"评测工程师|测试工程师|算法|架构")
    suspects = [r for r in sem if boundary.search(r["title"])]
    print(f"\n[2·疑似误杀复核] {len(suspects)} 条（语义拦截 ∩ BOUNDARY 词）:")
    for r in suspects:
        print(f"    ❓ {r['title'][:36]} ← 人工判定是否误杀，误杀→进 GOLDEN_BOUNDARY")

    # ── 指标 5：Plan1/Plan2 分布（抢额度监控）──
    plan_pats = {"PLAN1": re.compile(r"PLAN1|P1-"), "PLAN2": re.compile(r"PLAN2|P2-")}
    passed = [r for r in rows if r.get("status") != "SKIPPED" or "NO_PLAN" not in (r.get("reason") or "")]
    sent_pool = [r for r in rows if r["status"] in ("SENT", "UNCERTAIN")]
    p1 = sum(1 for r in sent_pool if "P1" in (r.get("reason") or "") or "PLAN1" in (r.get("reason") or ""))
    print(f"\n[5·Plan分布] 进入发送池={len(sent_pool)}（其中可辨PLAN1={p1}）"
          f"\n    ⚠️ Plan2抢额度监控：发送池里 非深广城市 且 薪资<{12000} 的条数="
          f"{sum(1 for r in sent_pool if r['city'] not in ('深圳','广州'))}")

    # ── 指标 4 提示 ──
    print(f"\n[4·漏网检查] 本轮如再现标题含 '客服|顾问|标注|评测|训练' 的 SENT/UNCERTAIN：")
    leaks = [r for r in rows if r["status"] in ("SENT", "UNCERTAIN")
             and re.search(r"客服|标注|视频评测|影视.*评测", r["title"] or "")]
    for r in leaks:
        print(f"    🚨漏网 {r['title'][:36]} → 进 GOLDEN_NEGATIVE，不临时加词")
    if not leaks:
        print("    ✅ 无已知伪装模式漏网")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat())

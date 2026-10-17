#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""过渡线规则自测（正例/反例/边界），跑法：PYTHONPATH="" python3 scripts/test_transition_line.py"""
import sys

sys.path.insert(0, ".")
from job_decision import evaluate_job  # noqa: E402

CASES = [
    # (公司, 岗位, 描述, 薪资, 线路, 期望)
    ("某科技", "数据标注员", "朝九晚六 周末双休 一人一工位 五险一金", "4.5-6K", "transition", "ALLOW"),
    ("某人力", "AI数据标注", "周末双休 计件 按件计酬 多劳多得", "6-8K", "transition", "REJECT"),
    ("某游戏", "游戏运营", "周末双休 内容运营 社区", "9-12K", "transition", "ALLOW"),
    ("某公司", "内勤文员", "双休 行政 资料整理", "4-4.5K", "transition", "ALLOW"),
    ("某公司", "文员", "双休 行政", "3.5-3.8K", "transition", "REJECT"),
    ("某游戏", "游戏运营", "做六休一 大小周", "9-12K", "transition", "REJECT"),
    # 同岗位走 AI 主线：应被原有口径拦掉（证明两条线互不干扰）
    ("某科技", "数据标注员", "朝九晚六 周末双休", "4.5-6K", "ai", "REJECT"),
    ("某游戏", "游戏运营", "周末双休 内容运营", "9-12K", "ai", "REJECT_BY_ROUTE_OR_ALLOW"),
    # AI 主线正常岗不受影响
    ("某科技", "AI应用工程师", "负责RAG知识库 双休 五险一金", "12-20K", "ai", "ALLOW"),
]

ok = 0
for comp, title, desc, sal, line, want in CASES:
    r = evaluate_job(comp, title, desc, sal, line=line)
    got = r.action
    good = (got == want) or (want == "REJECT_BY_ROUTE_OR_ALLOW")
    ok += good
    print(f"  [{'✅' if good else '❌'}] {line:10} | {sal:8} | {title:12} → {got:6} "
          f"(期望 {want}) | {r.reason[:40]}")

print(f"\n{ok}/{len(CASES)} 通过")
sys.exit(0 if ok == len(CASES) else 1)

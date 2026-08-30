#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guardrails v1.0 (2026-08-31) — 投递安全红线固化层。

目的：不依赖模型智力。任何模型/任何人启动投递前，main() 强制跑本检查，
安全关键配置被改松（薪资线下调/双休词被删/日限放大/夜禁放宽）→ 拒绝启动。
规则单一事实源：docs/GUARDRAILS.md。改规则 = 改文档+这里+回归，三者同步。
"""
import json
from pathlib import Path

GUARDRAILS_VER = "1.0"
# 红线下限（用户定稿，勿松）：
NIGHT_BAN_START_MAX = 22          # 夜间禁投开始不得晚于 22:00
NIGHT_BAN_END_MAX = 8             # 结束不得晚于 08:00
DAILY_CAP_MAX = 100               # 正常期日上限不得 >100
HOURLY_CAP_MAX = 15               # 单小时上限不得 >15
SALARY_HOME_MIN = 10              # 1w+ 硬线（本机，单位 K）
SALARY_AWAY_MIN = 10
# 双休红线：这些词一旦从正文排除词消失 → 拒绝启动
WEEKEND_MANDATORY_WORDS = ["单休", "大小周", "996", "夜班"]
# 实习硬过滤：标题排除词必须含
INTERN_MANDATORY_WORDS = ["实习", "实习生"]


class GuardrailError(Exception):
    pass


def check_safety(cfg: dict) -> list:
    """返回违规列表；空 = 通过。"""
    v = []
    s = cfg.get("safety", {}) or {}
    sb = s.get("night_ban_start", 23)
    eb = s.get("night_ban_end", 8)
    if sb > NIGHT_BAN_START_MAX:
        v.append(f"夜间禁投开始 {sb}:00 晚于红线 {NIGHT_BAN_START_MAX}:00（封号高风险）")
    if eb > NIGHT_BAN_END_MAX:
        v.append(f"夜间禁投结束 {eb}:00 晚于红线 {NIGHT_BAN_END_MAX}:00")
    dc = s.get("normal_daily_cap", 50)
    if dc > DAILY_CAP_MAX:
        v.append(f"日上限 {dc} > 红线 {DAILY_CAP_MAX}")
    hc = s.get("hourly_cap", 8)
    if hc > HOURLY_CAP_MAX:
        v.append(f"时上限 {hc} > 红线 {HOURLY_CAP_MAX}")
    return v


def check_salary(cfg: dict) -> list:
    v = []
    sf = cfg.get("salary_filter", {}) or {}
    # 采购线（line=procurement）沿用其历史下限 8K，其余一律 1w+ 硬线
    line = cfg.get("line", "ai")
    home = sf.get("home_min_accept", 0)
    away = sf.get("away_min_accept", 0)
    if line == "procurement":
        if home < 8 or away < 8:
            v.append(f"采购线薪资下限被改松 (home={home}, away={away}), 红线 8K")
    else:
        if home < SALARY_HOME_MIN or away < SALARY_AWAY_MIN:
            v.append(f"薪资 1w+ 硬线被改松 (home={home}K, away={away}K), 红线 {SALARY_HOME_MIN}K")
    return v


def check_exclude_words(cfg: dict) -> list:
    v = []
    body = cfg.get("body_exclude_keywords", []) or []
    missing = [w for w in WEEKEND_MANDATORY_WORDS if w not in body]
    if missing:
        v.append(f"正文排除词丢了双休红线词: {missing} —— 双休必须将被绕过")
    excl = cfg.get("exclude_keywords", []) or []
    imiss = [w for w in INTERN_MANDATORY_WORDS if w not in excl]
    if imiss:
        v.append(f"标题排除词丢了实习过滤词: {imiss} —— 实习岗将混入")
    return v


def run_all(cfg: dict) -> list:
    return check_safety(cfg) + check_salary(cfg) + check_exclude_words(cfg)


if __name__ == "__main__":
    import sys
    p = Path(__file__).parent / "config.json"
    cfg = json.loads(p.read_text())
    viol = run_all(cfg)
    if viol:
        print("🛑 GUARDRAILS 校验失败（拒绝投递）：")
        for x in viol:
            print("  ✗", x)
        sys.exit(2)
    print(f"✅ GUARDRAILS v{GUARDRAILS_VER} 校验通过（薪资/双休/实习/夜禁/日限/时限）")
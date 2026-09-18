#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guardrails v2.0 (2026-08-31) — 投递安全红线固化层（RULES_v2.0 配套）。

目的：不依赖模型智力。任何模型/任何人启动投递前，main() 强制跑本检查：
  A. 系统级硬红线（薪资分层参数不被改松/双休/实习词表在/夜禁/日限/时限）
  B. 岗位价值决策器 job_decision.py 的存在性与完整性（分层规则不可被删改）
规则单一事实源：docs/GUARDRAILS.md。改规则 = 改文档+这里+回归，三者同步。
"""
import json
import sys
from pathlib import Path

GUARDRAILS_VER = "2.0"
# 红线下限（用户定稿 RULES_v2.0，勿松）：
NIGHT_BAN_START_MAX = 22          # 夜间禁投开始不得晚于 22:00
NIGHT_BAN_END_MAX = 8             # 结束不得晚于 08:00
DAILY_CAP_MAX = 150               # 正常期日上限不得 >150（2026-09-05 冲刺模式：用户确认150目标）
HOURLY_CAP_MAX = 15               # 单小时上限不得 >15

# 分层薪资参数下限（可更严，不可更松；单位 K/月）：
SALARY_HARD_FLOOR_MIN = 5.0       # <5K 默认拒绝 —— 地板不得低于 5
SALARY_NORMAL_FLOOR_MIN = 8.0     # 8-10K 正常档 —— 不得低于 8
SALARY_PRIORITY_MIN = 10.0        # >=10K 优先档 —— 不得低于 10

# 双休红线：这些词一旦从正文排除词消失 → 拒绝启动
# 2026-09-16 用户定稿「至少双休」：大小周/单双休**回到硬排除词**（冲刺期的
# 「≥12K 可谈」特批已废除，见 check_decisioner 对 DXZ_WAIVER 的反向校验）。
WEEKEND_MANDATORY_WORDS = ["单休", "大小周", "单双休", "996", "夜班"]
# 实习硬过滤：标题排除词必须含
INTERN_MANDATORY_WORDS = ["实习", "实习生"]


class GuardrailError(Exception):
    pass


def check_safety(cfg: dict) -> list:
    """A1: 系统级安全参数（夜间/日限/时限）。"""
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
    """A2: 分层薪资参数完整性（RULES_v2.0）。

    旧版（v1.0）校验 home/away_min_accept>=10 —— 那是过时的 1w 硬线；
    v2.0 改为校验 job_decision.py 中的分层常量不可被改松。
    cfg 里只允许携带覆盖值（可更严），若缺失则读 job_decision 默认。
    """
    v = []
    # 优先读 config 覆盖（允许更严），否则读 job_decision 常量
    try:
        import job_decision as jd
        hard = cfg.get("salary_bands", {}).get("hard_floor", jd.SALARY_HARD_FLOOR)
        normal = cfg.get("salary_bands", {}).get("normal_floor", jd.SALARY_NORMAL_FLOOR)
        pri = cfg.get("salary_bands", {}).get("priority", jd.SALARY_PRIORITY)
    except Exception as e:
        v.append(f"job_decision.py 不可用: {str(e)[:60]} —— 分层决策器必须存在")
        return v
    if hard + 1e-9 < SALARY_HARD_FLOOR_MIN:
        v.append(f"薪资地板被改松 ({hard}K < 红线 {SALARY_HARD_FLOOR_MIN}K)")
    if normal + 1e-9 < SALARY_NORMAL_FLOOR_MIN:
        v.append(f"正常档被改松 ({normal}K < 红线 {SALARY_NORMAL_FLOOR_MIN}K)")
    if pri + 1e-9 < SALARY_PRIORITY_MIN:
        v.append(f"优先档被改松 ({pri}K < 红线 {SALARY_PRIORITY_MIN}K)")
    return v


def check_exclude_words(cfg: dict) -> list:
    """A3: 词表完整性（双休/实习硬过滤词不可被删）。"""
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


def check_decisioner(cfg: dict) -> list:
    """B: 岗位价值决策器完整性（分层规则不被删改）。

    只验「关键常量仍然存在且方向正确」，不锁死全部源码 ——
    决策器内部逻辑由 tests/test_job_decision.py 兜底。
    """
    v = []
    try:
        import job_decision as jd
        need = ["SALARY_HARD_FLOOR", "SALARY_NORMAL_FLOOR", "SALARY_PRIORITY",
                "WORKDAY_REDLINES", "SPECIAL_APPROVAL_SIGNALS"]
        for name in need:
            if not hasattr(jd, name):
                v.append(f"job_decision.py 缺失关键常量 {name} —— 分层规则被破坏")
        if not hasattr(jd, "evaluate_job"):
            v.append("job_decision.py 缺失 evaluate_job() —— 决策器被破坏")
        elif len(jd.WORKDAY_REDLINES) < 6:
            v.append("WORKDAY_REDLINES 过短（<6词）—— 制度红线被抽空")
        elif len(jd.SPECIAL_APPROVAL_SIGNALS) < 6:
            v.append("SPECIAL_APPROVAL_SIGNALS 过短（<6词）—— 特批通道被抽空")
        # 2026-09-16 用户定稿「至少双休」：job_decision 里**不得**再有大小周薪资豁免分支。
        # 历史事故形态：冲刺期给大小周开的特批通道（≥12K 放行）事后没人收回，
        # 于是高薪大小周被静默放行。这里反向校验，发现残留就拒绝启动。
        try:
            _src = Path(jd.__file__).read_text(encoding="utf-8")
            if 'hit == "大小周"' in _src:
                v.append("job_decision 仍有大小周薪资豁免分支（DXZ_WAIVER）—— 违反「至少双休」定稿")
            if "单双休" not in jd.WORKDAY_REDLINES:
                v.append("WORKDAY_REDLINES 缺「单双休」—— 一个月休六天会被当双休放行")
        except Exception:
            v.append("无法读取 job_decision.py 源码校验大小周豁免分支")
    except Exception as e:
        v.append(f"job_decision.py 不可用: {str(e)[:60]}")
    return v


def run_all(cfg: dict) -> list:
    return (check_safety(cfg) + check_salary(cfg) + check_exclude_words(cfg)
            + check_decisioner(cfg))


if __name__ == "__main__":
    p = Path(__file__).parent / "config.json"
    cfg = json.loads(p.read_text())
    viol = run_all(cfg)
    if viol:
        print("🛑 GUARDRAILS 校验失败（拒绝投递）：")
        for x in viol:
            print("  ✗", x)
        sys.exit(2)
    print(f"✅ GUARDRAILS v{GUARDRAILS_VER} 校验通过（分层薪资/双休/实习/夜禁/日限/时限/决策器）")
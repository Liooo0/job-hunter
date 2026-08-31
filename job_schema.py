#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JOB-HUNTER v5 统一岗位 Schema — 所有模型必须吐出的结构（2026-08-31）。

核心原则：模型负责"看懂岗位"，代码负责"决定命运"。
本模块不做判断，只做两件事：
  1. 定义 JobRecord：任何 LLM/解析器输出的岗位事实必须落进这个结构；
  2. evidence 校验：字段值没有原文证据支撑 → 降级为 UNKNOWN，防模型脑补。

Evidence Level（证据等级）：
  E0 无证据    → 字段不可信，降 UNKNOWN
  E1 模糊描述  → 可用于正向信号（双休），不可用于打死刑（红线按 E2+ 才拦截，
                除非字段本身即红线且来自标题）
  E2 明确JD    → 正常采信
  E3 HR明确回复 → 覆盖 JD（最高实践可信度）
  E4 多来源交叉 → 覆盖一切

用法：
    raw = llm.parse(jd_text)          # 模型返回 dict
    rec = JobRecord.from_dict(raw)
    rec = rec.enforce_evidence()      # 无证据字段 → UNKNOWN
    signals = rec.to_signals()        # 喂给 job_decision.evaluate_job()
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "5.0"

# 证据等级：值越大越可信
EVIDENCE_LEVELS = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}

UNKNOWN = "UNKNOWN"


def _ev_rank(level: str) -> int:
    return EVIDENCE_LEVELS.get(str(level or "E0").upper(), 0)


@dataclass
class FieldEvidence:
    """一个判断字段 = 值 + 证据列表。evidence 为空即 E0。"""
    value: Any = None
    evidence: List[str] = field(default_factory=list)
    level: str = "E0"

    def effective_level(self) -> str:
        """显式 level 与 evidence 数量取规则：无证据永远 E0。"""
        if not self.evidence:
            return "E0"
        # 多条独立证据自动视为 E4 以下不升级；显式声明优先
        return self.level if _ev_rank(self.level) >= 2 else ("E2" if len(self.evidence) >= 1 else "E1")


@dataclass
class JobRecord:
    """统一岗位 Schema。plan/route 由后续规则层填写，模型无权直接给结论。"""
    schema_version: str = SCHEMA_VERSION
    job_id: str = ""
    job_title: str = ""
    company: str = ""
    city: str = ""

    # 岗位分类（模型只允许从枚举里选，禁止自由发挥）
    category: str = UNKNOWN          # AI_APPLICATION/AI_DELIVERY/AI_OPS/AUTOMATION/
                                     # DIGITAL/TESTING/SUPPORT/OTHER/BLOCKED_ROLE
    plan_candidate: str = UNKNOWN    # PLAN1 / PLAN2 / BLOCK / UNKNOWN —— 由 plan_router 填
    plan_priority: str = ""          # P1-A..D / P2-A..C / ""

    salary: FieldEvidence = field(default_factory=FieldEvidence)      # {"min","max","unit"}
    work_schedule: FieldEvidence = field(default_factory=FieldEvidence)  # {"rest_days","shift","night"}
    employment: FieldEvidence = field(default_factory=FieldEvidence)  # {"type","outsourcing","travel"}
    difficulty: FieldEvidence = field(default_factory=FieldEvidence)  # {"required_years","level"}

    skills: List[str] = field(default_factory=list)
    ai_match: Optional[float] = None       # 0-1，参考值，不进硬规则
    risk_flags: List[str] = field(default_factory=list)
    raw_signals: Dict[str, Any] = field(default_factory=dict)  # 兜底：规则层旧信号

    # ── 构造 ──
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JobRecord":
        rec = cls()
        for k, v in d.items():
            if k in ("salary", "work_schedule", "employment", "difficulty"):
                if isinstance(v, dict) and ("value" in v or "evidence" in v):
                    setattr(rec, k, FieldEvidence(
                        value=v.get("value"),
                        evidence=[str(e) for e in v.get("evidence", []) if e],
                        level=v.get("level", "E0")))
                elif k == "salary" and isinstance(v, dict):
                    # 模型直给 {"min":10000,"max":15000} 的宽松形态
                    setattr(rec, k, FieldEvidence(value=v, evidence=d.get("salary_evidence", []),
                                                  level=d.get("salary_level", "E0")))
                elif not isinstance(v, dict):
                    setattr(rec, k, FieldEvidence(value=v, level="E0"))
            elif hasattr(rec, k) and k != "schema_version":
                setattr(rec, k, v)
        return rec

    # ── 证据优先：E0 字段禁止参与判决 ──
    def enforce_evidence(self) -> "JobRecord":
        """无证据字段 → value 置 None（UNKNOWN）。防模型脑补放行/误杀。"""
        for name in ("salary", "work_schedule", "employment", "difficulty"):
            fe: FieldEvidence = getattr(self, name)
            if fe.effective_level() == "E0":
                fe.value = None
        return self

    def has_evidence(self, name: str) -> bool:
        fe = getattr(self, name, None)
        return isinstance(fe, FieldEvidence) and fe.effective_level() != "E0"

    # ── 喂给现有 L2（job_decision.evaluate_job 吃 dict 信号）──
    def to_signals(self) -> Dict[str, Any]:
        """Schema → 旧信号 dict。有证据用 Schema 值，无证据回退 raw_signals。"""
        sig = dict(self.raw_signals)
        sig.setdefault("title", self.job_title)
        sig.setdefault("company", self.company)
        sig.setdefault("city", self.city)
        if self.has_evidence("salary"):
            v = self.salary.value or {}
            lo = v.get("min") or v.get("low")
            if lo:
                sig["salary_low"] = float(lo) / 1000.0   # 转 K 对齐 L2
        if self.has_evidence("work_schedule"):
            v = self.work_schedule.value or {}
            sig["rest_days"] = v.get("rest_days")
            sig["shift"] = bool(v.get("shift"))
            sig["night_shift"] = bool(v.get("night"))
        if self.has_evidence("employment"):
            v = self.employment.value or {}
            sig["outsourcing"] = bool(v.get("outsourcing"))
            sig["employment_type"] = v.get("type", "")
        return sig

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── 模型输出校验：越界字段一票降 E0 ──
VALID_CATEGORIES = {UNKNOWN, "AI_APPLICATION", "AI_DELIVERY", "AI_OPS", "AUTOMATION",
                    "DIGITAL", "TESTING", "SUPPORT", "OTHER", "BLOCKED_ROLE"}


def validate_model_output(d: Dict[str, Any]) -> List[str]:
    """返回违规列表（空 = 合规）。模型乱给结论字段（action/decision/reject）视为越权。"""
    errs = []
    for banned in ("action", "decision", "should_apply", "reject", "allow"):
        if banned in d:
            errs.append(f"模型越权输出决策字段: {banned}（模型只准提取事实）")
    cat = d.get("category")
    if cat and cat not in VALID_CATEGORIES:
        errs.append(f"category 越界: {cat}")
    ev = d.get("salary_evidence", d.get("salary", {}).get("evidence") if isinstance(d.get("salary"), dict) else None) or []
    if isinstance(d.get("salary"), dict) and d["salary"].get("min") and not ev:
        errs.append("salary 有值但无 evidence → 将降为 UNKNOWN")
    return errs

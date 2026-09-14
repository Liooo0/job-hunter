#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Block G —— 岗位真实性评估（MVP，2026-09-12）

移植自 career-ops-hq/career-ops 的 `modes/_shared.md`「Posting Legitimacy (Block G)」。

── 两条不可动摇的设计约束 ──

1. **不影响总分**。Block G 是**独立的定性评估**，不参与五维分/优先级排序。
   理由（原文）：总分必须跨历史可比——4.0 是"投/不投"的基线，一旦把真实性
   混进分数，analyze-patterns / funnel-velocity 折叠的历史趋势就全废了。
   本模块因此**只返回结果对象，不返回任何分数增量**（score_impact 恒为 "NONE"）。

2. **强制伦理框架**。① 目的是帮用户把时间花在真机会上；② **绝不表述为
   "指控对方不诚实"**；③ 只摆信号，让用户自己判断；④ **必须同时给出
   可疑信号的合理解释**（很多"可疑"都有正当理由）。

── 8 个信号，按可靠性加权（可靠性即证据强度，不是重要性）──
  high=3 / medium=2 / low=1
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

HIGH, MEDIUM, LOW = "high", "medium", "low"
_RELIABILITY_WEIGHT = {HIGH: 3, MEDIUM: 2, LOW: 1}

# ── 三档 ──
HIGH_CONFIDENCE = "high_confidence"      # 真实且活跃
CAUTION = "proceed_with_caution"         # 信号混杂，值得留意
SUSPICIOUS = "suspicious"                # 多个幽灵指标 → 先查再投

_TIER_ORDER = (HIGH_CONFIDENCE, CAUTION, SUSPICIOUS)
_TIER_LABEL = {
    HIGH_CONFIDENCE: "高置信（真实活跃）",
    CAUTION: "谨慎推进（信号混杂）",
    SUSPICIOUS: "可疑（建议先核实）",
}

# ── 具体技术信号（用于「JD 技术具体度」）──
SPECIFIC_TECH = [
    r"\bpython\b", r"\bsql\b", r"\brag\b", r"\bllm\b", r"\bagent\b", r"\bdify\b",
    r"\bfastgpt\b", r"\bcoze\b", r"\bprompt\b", r"\bembedding\b", r"向量", r"知识库",
    r"大模型", r"智能体", r"工作流", r"\bapi\b", r"\bdocker\b", r"\bkubernetes\b",
    r"\breact\b", r"\bjava\b", r"\bgo\b", r"\bragflow\b", r"\blangchain\b",
]
# 空话/愿景词：出现多、具体技术出现少 → 技术具体度低
VAGUE_MARKERS = [
    r"前沿", r"赋能", r"生态", r"闭环", r"抓手", r"拥抱", r"探索", r"关注",
    r"了解", r"学习", r"协助", r"意愿", r"热情", r"拼搏", r"狼性",
    r"cutting[- ]edge", r"fast[- ]paced", r"rock ?star", r"ninja",
]

# ── 要求矛盾检测（「要求自相矛盾」是强信号，含糊是弱信号）──
_YEAR_RE = re.compile(r"(\d+)\s*(?:年|years?)\s*(?:以上|及以上|\+)?", re.I)
_SENIOR_ONLY = [r"应届", r"在校", r"实习", r"无经验", r"fresh grad"]


@dataclass
class Signal:
    name: str
    value: str
    reliability: str
    verdict: str          # positive / concerning / neutral
    note: str = ""


@dataclass
class LegitimacyAssessment:
    tier: str
    signals: List[Signal] = field(default_factory=list)
    concerns: List[str] = field(default_factory=list)
    legitimate_explanations: List[str] = field(default_factory=list)
    # ★ 恒为 NONE —— 本评估绝不参与评分，这是设计约束不是占位
    score_impact: str = "NONE"

    @property
    def label(self) -> str:
        return _TIER_LABEL.get(self.tier, self.tier)

    def report(self) -> str:
        """人话报告。伦理框架在这里落地：摆信号 + 给合理解释 + 不下指控。"""
        lines = [f"【岗位真实性】{self.label}（不影响评分）"]
        for s in self.signals:
            mark = {"positive": "✅", "concerning": "⚠️", "neutral": "·"}.get(s.verdict, "·")
            lines.append(f"  {mark} {s.name}：{s.value}（可靠性 {s.reliability}）"
                         + (f" — {s.note}" if s.note else ""))
        if self.legitimate_explanations:
            lines.append("  可能的正当解释（请一并考虑）：")
            for e in self.legitimate_explanations:
                lines.append(f"    - {e}")
        lines.append("  说明：以上只是信号，不代表对方不诚信；请自行判断后可选择先联系核实。")
        return "\n".join(lines)


def _tech_specificity(jd: str) -> Signal:
    j = (jd or "").lower()
    specific = sum(1 for p in SPECIFIC_TECH if re.search(p, j, re.I))
    vague = sum(1 for p in VAGUE_MARKERS if re.search(p, j, re.I))
    if specific >= 3 and specific > vague:
        v, verdict, note = f"具体技术词 {specific} 个 / 空话词 {vague} 个", "positive", "职责写得较实"
    elif specific == 0 and vague >= 2:
        v, verdict, note = f"具体技术词 0 个 / 空话词 {vague} 个", "concerning", "JD 偏愿景"
    else:
        v, verdict, note = f"具体技术词 {specific} 个 / 空话词 {vague} 个", "neutral", ""
    return Signal("JD 技术具体度", v, MEDIUM, verdict, note)


def _requirements_realism(jd: str) -> Signal:
    """矛盾是强信号。典型矛盾：应届/无经验 与 N 年经验并存；学历要求倒挂。"""
    j = jd or ""
    conf = []
    for m in _YEAR_RE.finditer(j):
        yrs = int(m.group(1))
        if yrs >= 3:
            for s in _SENIOR_ONLY:
                if re.search(s, j, re.I):
                    # 只算同一句/邻近范围内才算真矛盾
                    span = j[max(0, m.start() - 40): m.end() + 40]
                    if re.search(s, span, re.I):
                        conf.append(f"同一处同时要求「{s}」与「{yrs} 年经验」")
    if conf:
        return Signal("要求是否自相矛盾", conf[0], MEDIUM, "concerning", "矛盾是强信号")
    if len(j.strip()) < 80:
        return Signal("要求是否自相矛盾", "JD 过短，无从判断", MEDIUM, "neutral", "含糊是弱信号")
    return Signal("要求是否自相矛盾", "未发现明显矛盾", MEDIUM, "positive")


def _posting_age(days: Optional[int], role_type: str = "") -> Signal:
    """挂多久。按岗位类型校正——高阶/稀缺岗挂得久属正常。"""
    if days is None:
        return Signal("岗位挂了多久", "未知", HIGH, "neutral", "缺此项，判档时不给正分")
    senior = any(k in (role_type or "") for k in ("管理", "总监", "专家", "架构", "负责人"))
    if days < 30:
        return Signal("岗位挂了多久", f"{days} 天", HIGH, "positive", "<30 天")
    if days < 60:
        return Signal("岗位挂了多久", f"{days} 天", HIGH, "neutral", "30-60 天，混杂区间")
    if senior:
        return Signal("岗位挂了多久", f"{days} 天", HIGH, "neutral", "高阶/稀缺岗挂得久属正常")
    return Signal("岗位挂了多久", f"{days} 天", HIGH, "concerning", "≥60 天")


def assess(*, posting_age_days: Optional[int] = None,
           apply_button_active: Optional[bool] = None,
           jd_text: str = "",
           recent_layoff_news: Optional[str] = None,
           repost_count_90d: int = 0,
           salary_transparent: Optional[bool] = None,
           role_company_fit: Optional[str] = None,
           role_type: str = "") -> LegitimacyAssessment:
    """评估一个岗位的真实性。返回独立对象 —— 绝不影响评分。"""
    sig: List[Signal] = []
    concerns: List[str] = []
    explains: List[str] = []

    # 1 岗位挂了多久（high）
    s = _posting_age(posting_age_days, role_type)
    sig.append(s)
    if s.verdict == "concerning":
        concerns.append(s.name)
        explains.append("公司走完编制审批常需数周，长期挂着未必是幽灵岗")

    # 2 Apply 按钮可点（high —— 可直接观测的事实）
    if apply_button_active is None:
        sig.append(Signal("Apply 按钮", "未采集", HIGH, "neutral", "这是最硬的活体证据，建议采集"))
    elif apply_button_active:
        sig.append(Signal("Apply 按钮", "可点击", HIGH, "positive", "最硬的活体证据"))
    else:
        sig.append(Signal("Apply 按钮", "不可点击", HIGH, "concerning"))
        concerns.append("Apply 按钮不可点击")
        explains.append("可能只是已招满但未下架，或页面缓存过期")

    # 3 JD 技术具体度（medium）
    sig.append(_tech_specificity(jd_text))

    # 4 要求是否自相矛盾（medium）
    sig.append(_requirements_realism(jd_text))
    if sig[-1].verdict == "concerning":
        concerns.append("要求自相矛盾")
        explains.append("JD 常由多人拼接，条件冲突往往是无心之失而非造假")

    # 5 近期裁员新闻（medium）
    if recent_layoff_news:
        sig.append(Signal("近期裁员新闻", recent_layoff_news, MEDIUM, "concerning"))
        concerns.append("近期裁员新闻")
        explains.append("裁员常在特定部门/地区，与新岗招聘可能并不冲突（尤其大公司）")
    else:
        sig.append(Signal("近期裁员新闻", "未发现", MEDIUM, "positive"))

    # 6 重复发布模式（medium）
    if repost_count_90d >= 2:
        sig.append(Signal("重复发布", f"90 天内 {repost_count_90d} 次", MEDIUM, "concerning"))
        concerns.append("90 天内重复发布")
        explains.append("陆续招多人（headcount>1）就会反复发布同一标题")
    else:
        sig.append(Signal("重复发布", f"90 天内 {repost_count_90d} 次", MEDIUM, "positive"))

    # 7 薪资是否透明（low —— 依司法辖区，不写有大量正当理由）
    if salary_transparent is True:
        sig.append(Signal("薪资透明", "JD 标注", LOW, "positive"))
    elif salary_transparent is False:
        sig.append(Signal("薪资透明", "未标注", LOW, "neutral", "可靠性低，不单独作证据"))
        explains.append("不写薪资在多数地区完全正常（谈薪策略/地区惯例）")
    else:
        sig.append(Signal("薪资透明", "未采集", LOW, "neutral"))

    # 8 岗位-公司匹配度（low —— 主观，只能当辅助）
    if role_company_fit:
        sig.append(Signal("岗位-公司匹配度", role_company_fit, LOW, "neutral", "主观，仅作辅助"))
        if any(k in role_company_fit for k in ("不符", "异常", "可疑")):
            concerns.append("岗位与公司主营不匹配")
            explains.append("公司可能正在新设业务线，或该岗为外包/供应商代招（前者正常，后者值得问清）")
    else:
        sig.append(Signal("岗位-公司匹配度", "未评估", LOW, "neutral"))

    # ── 定档：按可靠性加权累计 ──
    pos = sum(_RELIABILITY_WEIGHT[s.reliability] for s in sig if s.verdict == "positive")
    neg = sum(_RELIABILITY_WEIGHT[s.reliability] for s in sig if s.verdict == "concerning")
    high_neg = [s for s in sig if s.verdict == "concerning" and s.reliability == HIGH]
    # 高可靠性信号未采集 => 证据不足，不许给「高置信」
    # （2026-09-12 修：首版只数正分，导致"挂了多久/Apply按钮"都未采集时
    #   仅凭两条 medium 正分就判高置信——那是假自信）
    high_missing = [s for s in sig if s.verdict == "neutral" and s.reliability == HIGH
                    and ("未采集" in s.value or "未知" in s.value)]

    if high_neg or (neg >= 2 and neg > pos):
        tier = SUSPICIOUS
    elif high_missing:
        tier = CAUTION          # 证据不足 → 保守，不吹高置信
    elif neg >= 1:
        tier = CAUTION
    elif pos >= 3:
        tier = HIGH_CONFIDENCE
    else:
        tier = CAUTION

    return LegitimacyAssessment(tier=tier, signals=sig, concerns=concerns,
                                legitimate_explanations=explains)

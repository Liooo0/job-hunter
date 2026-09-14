#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""面试准备生成（MVP，2026-09-12）

移植自 career-ops-hq/career-ops 的 `modes/interview-prep.md`（MIT）里的两个核心设计：

1. **按"受众"分桶，不按轮次**。同一段经历在 HR 面前和在同级工程师面前的映射
   方式不同；一张不分受众的总表会**跨受众漂移**。
   四个桶：recruiter_screen / hiring_manager / peer_tech / panel_mixed

2. **故事库映射 + 缺口显性化**。每个受众的每个问题都要绑定故事库里的具体故事，
   并标 fit（strong / partial / none）。**none 就是缺口，必须显式列出来**，
   并给出可执行的补法："你需要一个关于 X 的故事。考虑：简历里的 Y 可做成 STAR+R"。

本 MVP 不调 LLM：问题从"JD 信号 → 问题模板"的确定性映射里出，
故事匹配用词重叠。因此**它不会编造你的经历，只会挑出你该补的缺口**。

另外沿用 career-ops 的两个实用提醒：
- **已口头说过的薪资要保持一致**（各轮数字互相矛盾是硬伤）
- **体力管理**：4 小时 onsite 会先把经验少的候选人榨干 → 标出最考深度的环节，
  把最新鲜的素材留给它
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# ── 四个受众桶 ──
RECRUITER = "recruiter_screen"
HM = "hiring_manager"
PEER = "peer_tech"
PANEL = "panel_mixed"

AUDIENCE_LABEL = {
    RECRUITER: "HR / 招聘官（筛资格与动机）",
    HM: "用人经理（要结果与判断力）",
    PEER: "同级工程师（考深度与协作）",
    PANEL: "混合组面（跨职能压力面）",
}
AUDIENCE_ORDER = (RECRUITER, HM, PEER, PANEL)

# STAR+R 的 R = Reflection（复盘）
STAR_R_LABELS = ("Situation", "Task", "Action", "Result", "Reflection")

# ── JD 信号 → 问题模板（确定性映射）──
_TECH = {
    "rag": [r"\brag\b", r"检索增强", r"知识库"],
    "agent": [r"\bagent\b", r"智能体", r"工作流"],
    "llm_api": [r"\bllm\b", r"大模型", r"模型 ?api", r"prompt"],
    "python": [r"\bpython\b"],
    "sql": [r"\bsql\b", r"数据库"],
    "delivery": [r"交付", r"实施", r"落地", r"部署"],
    "automation": [r"自动化", r"rpa", r"流程优化"],
    "data": [r"数据分析", r"数据治理", r"\bbi\b"],
}
_TECH_QUESTION = {
    "rag": ("知识库/检索这条链路你怎么设计？", PEER),
    "agent": ("你做过哪些 Agent/工作流编排？怎么控制它不跑偏？", PEER),
    "llm_api": ("说说你对模型 API 选型与成本控制的判断。", PEER),
    "python": ("你的 Python 能力边界在哪？最复杂的那个脚本解决了什么？", PEER),
    "sql": ("你怎么做数据取数和口径校验？", PEER),
    "delivery": ("从需求到上线，你怎么保证交付不掉链子？", HM),
    "automation": ("哪个流程是你自动化掉的？省了多少人力？", HM),
    "data": ("你如何判断一个数据结论是可信的？", HM),
}
_SENIOR_SIGNALS = [r"负责", r"主导", r"带团队", r"管理", r"架构", r"\blead\b"]
_BASE_QUESTIONS = {
    RECRUITER: [
        ("请用一分钟介绍你自己，以及为什么投这个岗位。", None),
        ("你现在/上一段在做什么？为什么想换？", None),
        ("期望薪资是多少？（★ 与此前任何一轮已说过的数字保持一致）", None),
        ("最快什么时候能到岗？", None),
    ],
    HM: [
        ("讲一个你独立从 0 到 1 交付的东西，最难的地方在哪？", "delivery"),
        ("如果资源只有一半，你会砍掉什么？", None),
        ("你怎么跟不懂技术的业务方对齐需求？", None),
    ],
    PEER: [
        ("挑一个你做过的系统，讲讲架构取舍。", None),
        ("你怎么做测试和排错？举个例子。", None),
    ],
    PANEL: [
        ("说一次你判断错了、后来纠正的经历。", None),
        ("跨部门推动一件事时，怎么处理不配合的人？", None),
    ],
}


@dataclass
class MappedStory:
    question: str
    audience: str
    story_title: str
    fit: str          # strong / partial / none
    note: str = ""


@dataclass
class PrepReport:
    company: str
    role: str
    audience_map: Dict[str, List[str]] = field(default_factory=dict)
    questions: Dict[str, List[str]] = field(default_factory=dict)
    mapping: List[MappedStory] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    reminders: List[str] = field(default_factory=list)

    def render(self) -> str:
        L = [f"【面试准备】{self.company} · {self.role}", ""]
        L.append("一、受众地图（每个受众关心什么）")
        for a in AUDIENCE_ORDER:
            qs = self.audience_map.get(a) or []
            if qs:
                L.append(f"  · {AUDIENCE_LABEL[a]}：{('；'.join(qs))[:120]}")
        L.append("")
        L.append("二、分受众问题 + 故事绑定")
        for a in AUDIENCE_ORDER:
            qs = self.questions.get(a) or []
            if not qs:
                continue
            L.append(f"  【{AUDIENCE_LABEL[a]}】")
            for q in qs:
                hit = next((m for m in self.mapping if m.audience == a and m.question == q), None)
                if hit and hit.fit in ("strong", "partial"):
                    mark = {"strong": "✅", "partial": "🔶"}.get(hit.fit, "·")
                    L.append(f"    {mark} {q}")
                    L.append(f"       → 用故事《{hit.story_title}》（{hit.fit}）{('：' + hit.note) if hit.note else ''}")
                elif hit and hit.fit == "n-a":
                    L.append(f"    · {q}  （非行为题：按事实答，无需故事）")
                else:
                    L.append(f"    ❌ {q}")
        L.append("")
        if self.gaps:
            L.append("三、缺口（这些必须补，否则现场会卡）")
            for g in self.gaps:
                L.append(f"  · {g}")
            L.append("")
        if self.reminders:
            L.append("四、提醒")
            for r in self.reminders:
                L.append(f"  · {r}")
        return "\n".join(L)


def _norm_words(s: str) -> set:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (s or "").lower())
    toks = set()
    for w in s.split():
        if re.search(r"[\u4e00-\u9fff]", w):
            if len(w) >= 2:
                toks.add(w)
            for i in range(len(w) - 1):
                toks.add(w[i:i + 2])
        elif len(w) >= 3:
            toks.add(w)
    return toks


def _overlap(a: str, b: str) -> float:
    ga, gb = _norm_words(a), _norm_words(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga)


def detect_tech_signals(jd: str) -> List[str]:
    j = jd or ""
    return [k for k, pats in _TECH.items() if any(re.search(p, j, re.I) for p in pats)]


def derive_questions(jd: str, role: str = "") -> Dict[str, List[str]]:
    """JD 信号 → 分受众问题（确定性，不编造）。"""
    out: Dict[str, List[str]] = {a: [] for a in AUDIENCE_ORDER}
    for a, items in _BASE_QUESTIONS.items():
        for q, _sig in items:
            out[a].append(q)
    for sig in detect_tech_signals(jd):
        q, aud = _TECH_QUESTION[sig]
        if q not in out[aud]:
            out[aud].append(q)
    # 高级别信号 → 给 HR/经理加一道"职责边界"问题
    if any(re.search(p, jd or "", re.I) for p in _SENIOR_SIGNALS):
        out[HM].append("这个岗说'负责'，请说清你**独立**负责的范围和协作的边界。")
    return out


def audience_map(jd: str) -> Dict[str, List[str]]:
    """每个受众最可能关心什么（用于开场时判断"这句话该说给谁听"）。"""
    tech = detect_tech_signals(jd)
    m = {
        RECRUITER: ["资格是否对得上（学历/年限/到岗时间）", "动机是否可信", "薪资是否在预算内"],
        HM: ["能不能交付结果", "判断力与取舍", "与业务方沟通"],
        PEER: [f"技术深度（{('/'.join(tech) or '岗位相关栈')}）", "协作与排错"],
        PANEL: ["压力下的判断", "跨职能推动"],
    }
    return m


# ── 问题分类：只有「行为类」问题才需要故事 ──
# 2026-09-12 修：首版把所有问题都当行为题，于是"请用一分钟自我介绍""期望薪资多少"
# 也被要求配 STAR+R 故事 —— 那是荒谬的缺口提示，会把真正的缺口淹掉。
_NON_BEHAVIORAL = [
    r"自我介绍", r"介绍一下", r"为什么投", r"为何投", r"期望薪资", r"薪资",
    r"到岗", r"入职时间", r"还有什么问题", r"反问", r"你有什么想问",
    r"introduce yourself", r"salary expectation", r"notice period",
]
_BEHAVIORAL_HINTS = [
    r"讲一个", r"说一次", r"举个例子", r"举一个", r"经历过", r"怎么做到的",
    r"如何处理", r"怎么处理", r"你怎么", r"如何保证", r"最难", r"失误",
    r"tell me about a time", r"give me an example",
]


def needs_story(question: str) -> bool:
    """这个问题是否该用故事（STAR+R）回答。

    默认 **True**（需要故事），只有明确的非行为题（自我介绍/薪资/到岗/反问）才算 False。
    取向说明：多标一个不需要故事的缺口成本很低，漏掉一个真缺口到了现场才贵——
    所以宁可多标。2026-09-12 首版用了"必须命中行为题关键词才算"的反向默认，
    结果 "如果资源只有一半你会砍掉什么" 这类判断题被静默当成非行为题。
    """
    q = question or ""
    if any(re.search(p, q, re.I) for p in _NON_BEHAVIORAL):
        return False
    return True


def map_story(question: str, audience: str, stories, strong: float = 0.30,
              partial: float = 0.14) -> MappedStory:
    """给一个问题挑最合适的故事。返回 fit=strong/partial/none/n-a。

    n-a = 非行为题（自我介绍/薪资/到岗），按事实答即可，不算缺口。
    """
    if not needs_story(question):
        return MappedStory(question, audience, "", "n-a", "非行为题：按事实答，无需故事")
    best, best_score = None, 0.0
    for s in stories:
        text = getattr(s, "raw", "") or ""
        sc = _overlap(question, " ".join([getattr(s, "title", ""), text]))
        # 受众偏好加权：故事若标了 best_for，与之匹配的受众加权
        labels = getattr(s, "labels", {}) or {}
        best_for = labels.get("best for questions about", "")
        if best_for and re.search(r"hr|招聘|motivation|动机", best_for, re.I) and audience == RECRUITER:
            sc += 0.05
        if best_for and re.search(r"技术|tech|deep|架构", best_for, re.I) and audience == PEER:
            sc += 0.05
        if sc > best_score:
            best, best_score = s, sc
    if best is None or best_score < partial:
        return MappedStory(question, audience, "", "none",
                           "故事库里没有能覆盖这个问题的素材")
    fit = "strong" if best_score >= strong else "partial"
    note = "" if fit == "strong" else f"相邻素材（重叠 {best_score:.2f}），需要重构角度"
    return MappedStory(question, audience, best.title, fit, note)


def build_prep(company: str, role: str, jd: str, stories: Sequence = (),
               cv_text: str = "", stated_comp: str = "",
               stage_count: int = 0) -> PrepReport:
    """生成一份分受众的面试准备报告（含缺口）。"""
    rep = PrepReport(company=company, role=role)
    rep.audience_map = audience_map(jd)
    rep.questions = derive_questions(jd, role)

    for a in AUDIENCE_ORDER:
        for q in rep.questions.get(a, []):
            m = map_story(q, a, stories)
            rep.mapping.append(m)
            if m.fit == "none":
                topic = q.rstrip("？?").strip()
                hint = ""
                if cv_text:
                    # 从简历里找一句与该问题最相关的经历作为"可改造素材"
                    # 阈值 0.20：太低会把无关行（如证书）当素材，反而误导
                    scored = sorted(((_overlap(topic, ln), ln.strip())
                                     for ln in cv_text.splitlines() if len(ln.strip()) > 12),
                                    reverse=True)
                    if scored and scored[0][0] >= 0.20:
                        hint = f"考虑简历里的这段：{scored[0][1][:60]}…"
                rep.gaps.append(f"你需要一个关于「{topic}」的故事。{hint or '从项目里挑一段做成 STAR+R'}"
                                f"（STAR+R = {'/'.join(STAR_R_LABELS)}）")

    if stated_comp:
        rep.reminders.append(
            f"★ 此前已经对面试官说过的薪资是「{stated_comp}」——本轮必须保持一致，"
            f"各轮数字互相矛盾比数字本身高低更伤")
    rep.reminders.append("★ 已说出口的每个数字都必须能在简历里找到逐字佐证"
                         "（跑 provenance.gate_outgoing_text 过一遍）")
    if stage_count >= 3:
        rep.reminders.append(
            f"★ 体力管理：本流程 {stage_count} 轮，经验少的候选人会在后半程先掉状态；"
            f"标出最考深度的环节（通常是同级工程师轮），把最新鲜的素材留给它")
    return rep

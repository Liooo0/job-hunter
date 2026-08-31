#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JOB-HUNTER v5 Plan 路由器（2026-08-31 定稿）— 确定性代码路由，零 LLM。

定位（v5 决策树第⑥步）：L2 给 ALLOW 之后，回答"这个岗位属于哪条计划、
按什么顺序消耗额度"：

    PLAN1-A/B/C/D  职业主线（AI应用/实施/产品/自动化），额度第一优先级
    PLAN2-A/B/C    收入扩展池（≥10K 且非错位），只填 Plan1 用剩的容量
    NO_PLAN        无归属 → 不消耗额度（等价于不进池）

铁律：
  - 路由是确定性规则（纯函数），和 job_decision 一样不经过模型 → 可以输出
    BLOCK 语义（Java后端15K 再高薪也不进 Plan2）；但 ValueScore 永不参与路由。
  - Plan2 入场薪资线随城市档上浮（relocation.plan2_salary_floor）：
    深圳/广州 10K、杭州/南京/成都 12K、其他 14K —— 异地阻力要用更高薪资支付。
  - Plan1 不受 Plan2 薪资线约束（职业主线 8K 也优先于 Plan2 15K）。
"""
from dataclasses import dataclass, field
from typing import List, Optional

from relocation import city_tier, load_location_cfg, plan2_floor_for_city

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None

PROFILE_PATH = "config/candidate_profile.yaml"

# 与 candidate_profile.career.plan1 同步的兜底表（无 yaml 环境用；有 yaml 时以 profile 为准）
_FALLBACK_PLAN1 = {
    "P1-A": ["AI应用工程师", "大模型应用", "Agent", "RAG", "知识库", "AI应用开发", "智能体开发"],
    "P1-B": ["AI实施", "AI交付", "AI解决方案", "AI自动化", "RPA", "工作流", "流程自动化"],
    "P1-C": ["AI产品", "AI产品运营", "AI训练师", "AI评测", "AI大模型运营"],
    "P1-D": ["自动化开发", "业务自动化", "AI工具链", "低代码实施", "Python+AI"],
}
# P1 关键词的英文宽松匹配（Boss 标题常见大小写混排）
_CI_HINTS = {"agent": "P1-A", "rag": "P1-A", "python+ai": "P1-D"}


@dataclass
class PlanRoute:
    plan: str                      # PLAN1 / PLAN2 / NO_PLAN
    priority: str = ""             # A/B/C
    reason: str = ""
    salary_low_k: float = 0.0
    signals: List[str] = field(default_factory=list)

    @property
    def slot(self) -> str:         # quota_scheduler.rank() 用的键
        return f"{self.plan}-{self.priority}" if self.plan.startswith("PLAN") else self.plan

    def __str__(self):
        return f"{self.plan}-{self.priority}" if self.priority else self.plan


def load_career_cfg(path: str = PROFILE_PATH) -> dict:
    """读 profile career + blocked_roles；yaml 缺失 → 兜底常量。"""
    if _yaml is not None:
        try:
            from pathlib import Path
            p = Path(path)
            if p.exists():
                prof = _yaml.safe_load(p.read_text()) or {}
                career = prof.get("career") or {}
                if career.get("plan1"):
                    return {"plan1": career["plan1"],
                            "blocked_roles": prof.get("blocked_roles") or [],
                            "plan2_salary_min": (career.get("plan2") or {}).get("salary_min", 10000)}
        except Exception:
            pass
    return {"plan1": _FALLBACK_PLAN1, "blocked_roles": [
        "数据标注", "内容审核", "图片审核", "文本审核", "销售", "客服", "电销",
        "短视频", "短剧", "漫剧", "视频剪辑", "算法工程师", "模型训练", "后端开发",
        "Java开发", "采购", "保险销售", "课程顾问"], "plan2_salary_min": 10000}


def _hit(text: str, words) -> Optional[str]:
    for w in words:
        if w.lower() in text:
            return w
    return None


def route_plan(company: str, title: str, desc: str, salary_low_k: float,
               city: str = "", cfg: Optional[dict] = None,
               special_approval: bool = False) -> PlanRoute:
    """L2 ALLOW 后的确定性 Plan 路由。

    salary_low_k: 薪资下限（K/月，job_decision.parse_salary_low 已算）；未知传 0。
    special_approval: L2 特批通道（编制/国企）→ 视作稳定岗进 P2-C 兜底，不被薪资线杀。
    """
    cc = cfg or load_career_cfg()
    text = (title or "") + " " + (desc or "") + " " + (company or "")
    low = float(salary_low_k or 0)

    # ── 1. Plan1 关键词路由（P1-A→D 短路，主线不看薪资线、不受 block 影响：
    #    "AI应用工程师(Java栈)" 是主线岗，不能被 block 词误杀） ──
    tl = text.lower()
    for pri in ("P1-A", "P1-B", "P1-C", "P1-D"):
        words = (cc["plan1"].get(pri) or _FALLBACK_PLAN1.get(pri) or [])
        kw = _hit(tl, words)
        if kw:
            return PlanRoute("PLAN1", priority=pri[-1], reason=f"职业主线:{pri}命中「{kw}」",
                             salary_low_k=low, signals=[f"plan1_kw:{kw}"])
    for hint_kw, pri in _CI_HINTS.items():
        if hint_kw in tl:
            return PlanRoute("PLAN1", priority=pri[-1], reason=f"职业主线:{pri}命中「{hint_kw}」",
                             salary_low_k=low, signals=[f"plan1_ci:{hint_kw}"])

    # ── 2. 职业错位硬拦：只看标题（薪资再高也不进池；Plan2 不是垃圾岗位池）。
    #    只匹配 title 防止 desc 技术栈词误伤（"会Java"的AI岗 ≠ Java后端） ──
    blocked = _hit(title or "", cc["blocked_roles"])
    if blocked:
        return PlanRoute("NO_PLAN", reason=f"岗位错位:{blocked}(标题命中,薪资{low:.0f}K不改变路由)",
                         salary_low_k=low, signals=[f"blocked:{blocked}"])

    # ── 3. Plan2：收入扩展池，门槛随城市档上浮 ──
    floor_k = plan2_floor_for_city(city) / 1000.0
    if special_approval:
        return PlanRoute("PLAN2", priority="C", reason="特批稳定岗→P2-C(不受薪资线约束)",
                         salary_low_k=low, signals=["special_approval"])
    if low == 0:
        # 薪资未知：给面议岗一条缝（P2-C 最低优先），未知≠高薪扩张
        return PlanRoute("PLAN2", priority="C", reason="薪资未知→P2-C垫底",
                         salary_low_k=low, signals=["salary_unknown"])
    if low >= floor_k:
        tier = _plan2_tier(text)
        return PlanRoute("PLAN2", priority=tier,
                         reason=f"收入扩展:{low:.0f}K≥{city or '?'}/{city_tier(city)}档线{floor_k:.0f}K",
                         salary_low_k=low, signals=[f"p2_floor:{floor_k:.0f}K@{city_tier(city)}"])
    # 未到该城市档的 Plan2 线 → 不进池（外地阻力要用更高薪资支付）
    return PlanRoute("NO_PLAN", reason=f"非主线且{low:.0f}K<{floor_k:.0f}K({city_tier(city)}档Plan2线)",
                     salary_low_k=low, signals=["below_plan2_floor"])


def order_keywords_by_plan(keywords: List[str], cfg: Optional[dict] = None) -> List[str]:
    """搜索调度层（用户定稿：地区/主线优先级同时作用于"搜索顺序"和"最终评分"）。
    关键词按 Plan1 档位 P1-A → P1-B → P1-C → P1-D 重排；命中不了的排最后（保持原序）。
    稳定排序：同档不交换相对顺序，重复运行结果一致（验收标准2）。"""
    plan1 = (cfg or load_career_cfg())["plan1"]
    rank = {w: i for i, tier in enumerate(("P1-A", "P1-B", "P1-C", "P1-D"))
            for w in plan1.get(tier, [])}

    def _key(kw):
        kl = kw.lower()
        for w, r in rank.items():
            if w.lower() in kl or kl in w.lower():
                return r
        return 99
    return sorted(keywords, key=_key)


def _plan2_tier(text: str) -> str:
    """Plan2 内部分档：A=AI周边 B=数字化/自动化/产品/项目 C=一般相关。"""
    if _hit(text, ["数据", "测试", "评估", "标注平台", "语料", "提示词"]):
        return "A"
    if _hit(text, ["数字化", "自动化", "产品", "项目", "运营", "实施", "交付", "顾问"]):
        return "B"
    return "C"


if __name__ == "__main__":
    # 用户定稿验收样例
    from job_decision import evaluate_job, parse_salary_low
    cases = [
        ("深圳", "AI应用工程师", "RAG知识库开发,双休", "12-20K"),
        ("深圳", "AI产品经理", "负责AI产品规划,双休", "12-15K"),
        ("杭州", "自动化工程师", "产线自动化,双休", "11-13K"),
        ("杭州", "自动化工程师", "产线自动化,双休", "10-12K"),
        ("南京", "智能座舱测试", "整车测试,双休", "13-16K"),
        ("深圳", "Java后端", "SpringBoot微服务", "15-20K"),
        ("深圳", "算法工程师", "PyTorch模型训练", "20-40K"),
        ("北京", "销售顾问", "SaaS销售", "15-30K"),
    ]
    print(f"{'城市':4s} {'标题':14s} {'薪资':9s} 路由 → 原因")
    for city, title, desc, salary in cases:
        dec = evaluate_job("", title, desc, salary, city=city)
        if dec.action != "ALLOW":
            print(f"{city:4s} {title:14s} {salary:9s} L2拦截({dec.reason})")
            continue
        low = parse_salary_low(salary)
        r = route_plan("", title, desc, low, city=city, special_approval=dec.special_approval)
        print(f"{city:4s} {title:14s} {salary:9s} {r.slot:9s} → {r.reason}")

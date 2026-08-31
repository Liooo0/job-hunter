#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JOB-HUNTER v5.1 地区可达性评估（2026-08-31 定稿）— 纯函数层，无 DB 无副作用。

背景（用户定稿）：候选人所在地本身就是招聘筛选条件。HR 看到"深圳"投杭州岗，
第一反应不是技能而是"人不在本地，愿不愿意来？"。地区必须从 Value Score
的普通加分项，提升为贯穿全系统的可达性判断。

本模块输出三样东西，供三处消费：
  1. location_score（0-15）→ value_score v1.1 的地区维度
  2. relocation_risk（none/low/mid/high/extreme + 接受异地时下调）→ 展示/报告
  3. plan2_salary_floor（S/A=10K B=12K C=14K）→ quota_scheduler 的 Plan2 入场线

铁律：
  - 地区不是绝对硬门槛：S/A/B/C 永不产生 REJECT（裁决永远只归 job_decision）；
    "仅限本地" 也只把评分打到 0，不拦——那是 HR 的判断，不是我们的红线。
  - 单一事实源 = config/candidate_profile.yaml location 段，本文件不写死城市表。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None
_HAS_YAML = _yaml is not None

PROFILE_PATH = Path(__file__).resolve().parent / "config" / "candidate_profile.yaml"

# 地区维度满分（v1.1 权重表：地区可达性 15 分）
MAX_SCORE = 15

# 档内基准分（用户定稿）：深圳15 广州13 杭州/南京/成都8 其他3
TIER_BASE = {"S": 15, "A": 13, "B": 8, "C": 3}

# 信号修正（用户定稿）：明示接受异地 +3（封顶15）；明示仅限本地 -10（母城豁免）
REMOTE_BONUS = 3
LOCAL_ONLY_PENALTY = 10

# 候选人母城（人在深圳；"仅深圳本地"对深圳候选人零阻力）
HOME_CITY = "深圳"

# 异地风险档（展示用）
TIER_RISK = {"S": "none", "A": "low", "B": "mid", "C": "high"}

_LOCAL_ONLY_RE = re.compile(r"仅.{0,3}本地|只考虑本地|不考虑异地|不接受异地|需本地到岗|本地优先.{0,6}勿扰")
_LOCAL_PREFER_RE = re.compile(r"本地优先")
_REMOTE_RE = re.compile(r"远程|居家|异地(候选人|候选|可|均可)|接受.{0,4}异地|搬迁补贴|提供住宿|包住")


@dataclass
class LocationEval:
    city: str
    tier: str                        # S/A/B/C
    score: int                       # 0-15（地区可达性维度分，进 ValueScore 总分）
    risk: str                        # none/low/mid/high/extreme（异地风险）
    remote_accepted: bool = False    # JD/HR 明示接受异地或远程
    local_only: bool = False         # JD 明示仅限本地（极高风险，评分归零，但不 REJECT）
    signals: List[str] = field(default_factory=list)
    plan2_salary_floor: int = 10000  # 该城市的 Plan2 入场薪资线（元/月）


def load_location_cfg(path: Optional[Path] = None) -> dict:
    """读 profile 的 location 段；无 yaml 库或文件缺失 → 内置兜底（与 v5.1 定稿一致）。"""
    if _HAS_YAML:
        p = path or PROFILE_PATH
        if p.exists():
            loc = (_yaml.safe_load(p.read_text()) or {}).get("location") or {}
            if loc.get("tier"):
                return loc
    # 兜底（保持行为一致，不静默降级成无分级）
    return {
        "tier": {"S": ["深圳"], "A": ["广州"], "B": ["杭州", "南京", "成都"], "C": ["其他"]},
        "plan2_salary_floor": {"S": 10000, "A": 10000, "B": 12000, "C": 14000},
    }


def city_tier(city: str, loc: Optional[dict] = None) -> str:
    loc = loc or load_location_cfg()
    tier = loc.get("tier") or {}
    for t in ("S", "A", "B"):
        if city in (tier.get(t) or []):
            return t
    return "C"


def plan2_floor_for_city(city: str, loc: Optional[dict] = None) -> int:
    loc = loc or load_location_cfg()
    floors = loc.get("plan2_salary_floor") or {}
    return int(floors.get(city_tier(city, loc), 10000))


def _detect(text: str, loc: dict):
    """JD 文本证据探测：仅限本地 / 本地优先 / 接受异地·远程。

    优先用 profile 的 remote_signals 词表；正则兜底抓变体（"仅限深圳本地"等）。
    """
    rs = loc.get("remote_signals") or {}
    local_only = bool(_LOCAL_ONLY_RE.search(text)) or any(w in text for w in rs.get("local_only") or [])
    remote = any(w in text for w in rs.get("accept") or []) or bool(_REMOTE_RE.search(text))
    prefer = bool(_LOCAL_PREFER_RE.search(text)) or any(w in text for w in rs.get("local_prefer") or [])
    return local_only, remote, prefer


def evaluate_location(city: str, jd_text: str = "", loc: Optional[dict] = None) -> LocationEval:
    """地区可达性评估。city 空（远程岗常见）按 B 档处理但视同已接受异地。"""
    loc = loc or load_location_cfg()
    text = jd_text or ""

    if not city:  # 未标注城市 ≈ 远程/不限
        ev = LocationEval(city="(未注明)", tier="B", score=8, risk="mid",
                          plan2_salary_floor=plan2_floor_for_city("", loc))
        ev.remote_accepted = True
        ev.score = min(MAX_SCORE, TIER_BASE["B"] + 3)
        ev.risk = "low"
        ev.signals.append("未注明城市≈远程/不限")
        return ev

    tier = city_tier(city, loc)
    ev = LocationEval(city=city, tier=tier, score=TIER_BASE[tier],
                      risk=TIER_RISK[tier],
                      plan2_salary_floor=plan2_floor_for_city(city, loc))
    ev.signals.append(f"{city}={tier}档({TIER_BASE[tier]}分)")

    local_only, remote, prefer = _detect(text, loc)
    if local_only and city != HOME_CITY:
        # 明示"仅限本地/不接受异地" → -10 分 + 极高风险（不 REJECT，裁决不归这里）
        ev.local_only = True
        ev.risk = "extreme"
        ev.score = max(0, ev.score - LOCAL_ONLY_PENALTY)
        ev.signals.append(f"明示仅限本地(-{LOCAL_ONLY_PENALTY},风险极高)")
    elif local_only:
        ev.signals.append(f"仅限本地但=母城{HOME_CITY}(豁免)")
    if remote:
        # 明示接受异地/远程 → 风险降一档 + 加分（上限 15）
        ev.remote_accepted = True
        ev.risk = {"extreme": "mid", "high": "mid", "mid": "low", "low": "none",
                   "none": "none"}.get(ev.risk, ev.risk)
        ev.score = min(MAX_SCORE, ev.score + REMOTE_BONUS)
        ev.signals.append(f"明示接受异地/远程(+{REMOTE_BONUS},风险下调)")
    elif prefer and tier in ("B", "C") and not local_only:
        # "本地优先" → 只减分不拦截
        ev.score = max(0, ev.score - 2)
        ev.signals.append("本地优先(仅减分,-2)")
    return ev


# 搜索调度层顺序（用户定稿：地区优先级同时作用于搜索顺序和最终评分）
TIER_SEARCH_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}


def order_cities_by_tier(cities, loc: Optional[dict] = None) -> List[str]:
    """S 深圳 → A 广州 → B 杭州/南京/成都 → C 其他；档内保持传入顺序。"""
    loc = loc or load_location_cfg()
    return sorted([c for c in cities if c],
                  key=lambda c: TIER_SEARCH_ORDER.get(city_tier(c, loc), 9))


if __name__ == "__main__":
    # 用户定稿的验收样例（人话演示）
    cases = [
        ("深圳", "AI应用工程师,双休"),
        ("杭州", "AI应用工程师,双休"),
        ("杭州", "AI应用工程师,15-20K,接受优秀异地候选人"),
        ("南京", "AI实施,仅限南京本地"),
        ("南京", "AI实施,本地优先"),
        ("成都", "Agent开发,可远程"),
        ("武汉", "RAG工程师,14-18K"),
        ("", "AI训练师,长期远程"),
    ]
    for city, jd in cases:
        e = evaluate_location(city, jd)
        print(f"{e.city:6s} {e.tier}档 {e.score:2d}/15 风险={e.risk:7s} "
              f"Plan2线={e.plan2_salary_floor}  {'; '.join(e.signals)}")

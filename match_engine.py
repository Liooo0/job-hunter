#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用法:
  PYTHONPATH="" /usr/bin/python3 match_engine.py "AI应用工程师" --desc "负责RAG知识库与Agent工作流开发，Python/FastAPI" --salary "15-25K" --city 深圳
  PYTHONPATH="" /usr/bin/python3 match_engine.py "AI工程师" --desc-file jd.txt --json

统一岗位评分引擎（评估层）— 把资格层信号 + 关键词命中明细映射成 README 五维加权总分。
纯确定性规则，LLM 不参与，零第三方依赖。与 shared.score_jd / smart_filter / deep_filter
三层「资格层」串联：资格层决定能不能投，本引擎决定值不值得投并给出可解释报告。

═══════════════════════════════════════════════════════════════
维度计分规则（全部确定性，clamp 到 [0,100]）
═══════════════════════════════════════════════════════════════
technical (权重0.30)
  - cfg.skills 在 标题+正文 命中数 n（contains_kw 词边界匹配）→ 基线 min(40 + n*12, 95)
  - boost_keywords 每命中一个 +4，封顶 +15
  - 正文含 deep_filter.TRADITIONAL_STACK 传统栈 且 标题+正文无 deep_filter.PYTHON_AI_SAFE
    保护词 → -20（标题党兜底：宣称AI实为传统开发）

direction (权重0.30)
  - cfg.target_roles 存在且标题命中 → 95（优先于 job_pools）
  - 标题命中 job_pools.keywords 分组词：S级 → 90；A级 → 70；B级 → 50；组间取最高档
  - 无分组命中但 boost_keywords 命中标题 → 60
  - 全不中 → 30
  - target_roles 缺失时从 job_pools 兜底映射（见 load_candidate_profile），不崩

experience (权重0.15)，按标题+正文判断，取最先命中的档：
  - 高级/资深/8年以上/10年以上 → 20
  - 经验不限/应届优先/1年以内/应届生 → 85
  - 3年以上/5年以上 → 35
  - 无明显经验表述 → 70

culture (权重0.15)，基线75：
  - 正文残余风险词 大小周/996/单休/夜班 每项 -30（正常已被 smart_filter 拦，此处兜底扣分）
  - 公司名命中 shared.KNOWN_OUTSOURCING 外包名单 → -15
  - 正文命中 shared.BOOST_PROJECTS 高价值项目（特斯拉/蔚来/智驾等）→ +10

location (权重0.10)：
  - 正文含「远程」→ 95
  - city == 深圳(primary) → 90；salary_filter.home_cities 其余 → 80
  - city_pools.city_priority secondary → 70；opportunistic → 55；未知城市 → 50
  - 城市名去尾部「市」后匹配

总分 = Σ(score*weight) 四舍五入取整；verdict：>=75 strong_apply / 60-74 apply /
45-59 consider / <45 skip。
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deep_filter import PYTHON_AI_SAFE, TRADITIONAL_STACK  # noqa: E402
from shared import BOOST_PROJECTS, KNOWN_OUTSOURCING, load_config, score_jd  # noqa: E402

WEIGHTS = {
    "technical": 0.30,
    "direction": 0.30,
    "experience": 0.15,
    "culture": 0.15,
    "location": 0.10,
}

DIM_LABELS = {
    "technical": "技术匹配",
    "direction": "职业方向",
    "experience": "经验匹配",
    "culture": "文化匹配",
    "location": "地点通勤",
}

VERDICT_LABELS = {
    "strong_apply": "强烈推荐",
    "apply": "可以投",
    "consider": "斟酌",
    "skip": "跳过",
}

# 经验分档词表（顺序即优先级：高级 > 应届友好 > 中级年限）
EXPERIENCE_SENIOR = ["高级", "资深", "8年以上", "10年以上"]
EXPERIENCE_JUNIOR = ["经验不限", "应届优先", "1年以内", "应届生"]
EXPERIENCE_MID = ["3年以上", "5年以上"]

# culture 兜底作息风险词（smart_filter 已拦大部分，这里只做扣分不放行）
CULTURE_RISK_WORDS = ["大小周", "996", "单休", "夜班"]

# gaps 候选词：JD 提到但不在 skills 表的相关技术缺口
RELEVANT_TERMS = [
    "LangChain", "LangGraph", "Embedding", "向量数据库", "FastAPI", "MCP",
    "Prompt", "Function Calling", "微调", "SFT", "多模态", "Transformer",
    "RAG", "Agent", "LLM", "大模型", "知识库", "工作流", "智能体",
    # 以下为通用 AI 岗常见要求，兜底词表缺失时保证缺口识别仍可用：
    "Python", "Dify", "Coze", "机器学习", "深度学习", "NLP", "数据分析",
]

# 与 shared.contains_kw 相同的词边界策略（独立缓存，避免跨模块状态）
_PATTERN_CACHE: Dict[str, "re.Pattern"] = {}


def _kw_pattern(kw_lower: str) -> "re.Pattern":
    pat = _PATTERN_CACHE.get(kw_lower)
    if pat is None:
        body = re.escape(kw_lower)
        left = r"(?<![A-Za-z0-9])" if re.match(r"[a-z0-9]", kw_lower[:1]) else ""
        right = r"(?![A-Za-z0-9])" if re.match(r"[a-z0-9]", kw_lower[-1:]) else ""
        pat = re.compile(left + body + right, re.IGNORECASE)
        _PATTERN_CACHE[kw_lower] = pat
    return pat


def contains_kw(haystack: str, kw: str) -> bool:
    """ASCII 词边界匹配；中文退化为子串。空串安全。"""
    if not haystack or not kw:
        return False
    return bool(_kw_pattern(kw.lower()).search(haystack))


def verdict_for(total: int) -> str:
    """阈值：>=75 强烈推荐 / 60-74 可以投 / 45-59 斟酌 / <45 跳过。"""
    if total >= 75:
        return "strong_apply"
    if total >= 60:
        return "apply"
    if total >= 45:
        return "consider"
    return "skip"


def load_candidate_profile(cfg: Optional[dict]) -> dict:
    """从 config 归一化候选画像。

    返回 {target_roles, pools:{S级,A级,B级}, home_cities, city_priority, notes}
    config.json 缺 target_roles 时从 job_pools.keywords 兜底映射，并在 notes 注明。
    """
    cfg = cfg or {}
    notes_parts: List[str] = []

    target_roles = list(cfg.get("target_roles") or [])
    if target_roles:
        notes_parts.append("target_roles 来自 config")
    else:
        notes_parts.append("target_roles 缺失，已从 job_pools.keywords 兜底映射")

    pools: Dict[str, List[str]] = {"S级": [], "A级": [], "B级": []}
    job_pools = (cfg.get("job_pools") or {}).get("keywords") or {}
    for group_name, words in job_pools.items():
        tier = group_name.split("-", 1)[0]
        if tier in pools:
            pools[tier].extend(words)

    salary_filter = cfg.get("salary_filter") or {}
    home_cities = list(salary_filter.get("home_cities") or ["深圳"])
    city_priority = dict((cfg.get("city_pools") or {}).get("city_priority") or {})

    return {
        "target_roles": target_roles,
        "pools": pools,
        "home_cities": home_cities,
        "city_priority": city_priority,
        "notes": "；".join(notes_parts),
    }


def _score_technical(title: str, desc: str, cfg: dict,
                     evidence: List[str], risks: List[str]) -> int:
    combined = (title or "") + " " + (desc or "")
    skill_hits = [s for s in (cfg.get("skills") or []) if contains_kw(combined, s)]
    base = min(40 + len(skill_hits) * 12, 95)
    if skill_hits:
        evidence.append("技能命中: " + "/".join(skill_hits[:6]))
    else:
        evidence.append("无技能命中(基线40)")

    boost_hits = [b for b in (cfg.get("boost_keywords") or []) if contains_kw(combined, b)]
    boost_bonus = min(len(boost_hits) * 4, 15)
    if boost_hits:
        evidence.append(f"加分词+{boost_bonus}: " + "/".join(boost_hits[:5]))

    score = base + boost_bonus

    trad_hits = [t for t in TRADITIONAL_STACK if contains_kw(desc or "", t)]
    protected = any(contains_kw(combined, p) for p in PYTHON_AI_SAFE)
    if trad_hits and not protected:
        score -= 20
        risks.append("传统栈无AI保护词-20")
        evidence.append("传统栈: " + "/".join(trad_hits[:4]) + " → -20")

    return _clamp(score)


def _score_direction(title: str, profile: dict, cfg: dict,
                     evidence: List[str]) -> int:
    t = title or ""
    if profile["target_roles"]:
        for role in profile["target_roles"]:
            if contains_kw(t, role):
                evidence.append(f"目标岗位命中: {role}")
                return 95

    tier_scores = [("S级", 90), ("A级", 70), ("B级", 50)]
    best_tier = None
    for tier, tscore in tier_scores:
        hit_words = [w for w in profile["pools"].get(tier, []) if contains_kw(t, w)]
        if hit_words and best_tier is None:
            best_tier = (tier, tscore, hit_words[0])
            evidence.append(f"{tier}方向: {hit_words[0]}")
            break

    if best_tier:
        return best_tier[1]

    boost_hit = next((b for b in (cfg.get("boost_keywords") or []) if contains_kw(t, b)), None)
    if boost_hit:
        evidence.append(f"仅加分词命中方向: {boost_hit}")
        return 60

    evidence.append("标题未命中任何方向词")
    return 30


def _score_experience(title: str, desc: str, evidence: List[str],
                      risks: List[str]) -> int:
    combined = (title or "") + " " + (desc or "")
    for kw in EXPERIENCE_SENIOR:
        if contains_kw(combined, kw):
            risks.append(f"高级年限门槛: {kw}")
            evidence.append(f"高级/年限门槛: {kw}")
            return 20
    for kw in EXPERIENCE_JUNIOR:
        if contains_kw(combined, kw):
            evidence.append(f"经验友好: {kw}")
            return 85
    for kw in EXPERIENCE_MID:
        if contains_kw(combined, kw):
            evidence.append(f"中级年限门槛: {kw}")
            return 35
    evidence.append("无明显经验表述")
    return 70


def _score_culture(company: str, desc: str, evidence: List[str],
                   risks: List[str]) -> int:
    score = 75
    d = desc or ""
    risk_hits = [w for w in CULTURE_RISK_WORDS if contains_kw(d, w)]
    for w in risk_hits:
        score -= 30
        risks.append(f"作息风险: {w}(-30)")
    if risk_hits:
        evidence.append("残余风险词: " + "/".join(risk_hits))

    comp = company or ""
    out_hit = next((k for k in KNOWN_OUTSOURCING if contains_kw(comp, k)), None)
    if out_hit:
        score -= 15
        risks.append("外包公司(-15)")
        evidence.append(f"外包名单命中: {out_hit}")

    boost_hit = next((p for p in BOOST_PROJECTS if contains_kw(d, p)), None)
    if boost_hit:
        score += 10
        evidence.append(f"高价值项目: {boost_hit}(+10)")

    if not (risk_hits or out_hit or boost_hit):
        evidence.append("无文化风险/加分信号(默认75)")
    return _clamp(score)


def _norm_city(city: str) -> str:
    c = (city or "").strip()
    return c[:-1] if c.endswith("市") else c


def _score_location(city: str, desc: str, profile: dict,
                    evidence: List[str]) -> int:
    d = desc or ""
    if contains_kw(d, "远程"):
        evidence.append("正文支持远程")
        return 95
    c = _norm_city(city)
    if c == "深圳":
        evidence.append("深圳=primary城市")
        return 90
    if c in [_norm_city(x) for x in profile["home_cities"]]:
        evidence.append(f"{c}=home_cities")
        return 80
    tier = profile["city_priority"].get(c)
    if tier == "secondary":
        evidence.append(f"{c}=secondary城市")
        return 70
    if tier == "opportunistic":
        evidence.append(f"{c}=opportunistic城市")
        return 55
    evidence.append(f"{c or '空'}=未知城市")
    return 50


def _find_gaps(title: str, desc: str, cfg: dict) -> List[str]:
    combined = (title or "") + " " + (desc or "")
    skills_lower = {str(s).lower() for s in (cfg.get("skills") or [])}
    boosts_lower = {str(b).lower() for b in (cfg.get("boost_keywords") or [])}
    gaps = []
    for term in RELEVANT_TERMS:
        tl = term.lower()
        # 已是用户技能/加分词的，命中属于"匹配"而非"缺口"
        if tl in skills_lower or tl in boosts_lower:
            continue
        if contains_kw(combined, term):
            gaps.append(term)
    return gaps[:8]


def _aggregate(dimensions: Dict[str, dict]) -> Tuple[int, float]:
    total_weighted = sum(d["weighted"] for d in dimensions.values())
    return int(total_weighted + 0.5), total_weighted


def _clamp(x: int) -> int:
    return max(0, min(100, x))


def explain_match(title: str, desc: str = "", company: str = "", salary: str = "",
                  city: str = "深圳", cfg: Optional[dict] = None) -> dict:
    """统一五维评分入口。返回可解释结果 dict（字段结构见模块 docstring）。"""
    cfg = cfg or load_config()
    profile = load_candidate_profile(cfg)

    risks: List[str] = []
    tech_ev: List[str] = []
    technical = _score_technical(title, desc, cfg, tech_ev, risks)
    direction_ev: List[str] = []
    direction = _score_direction(title, profile, cfg, direction_ev)
    exp_ev: List[str] = []
    experience = _score_experience(title, desc, exp_ev, risks)
    cul_ev: List[str] = []
    culture = _score_culture(company, desc, cul_ev, risks)
    loc_ev: List[str] = []
    location = _score_location(city, desc, profile, loc_ev)

    dimensions = {}
    for name, score in (("technical", technical), ("direction", direction),
                        ("experience", experience), ("culture", culture),
                        ("location", location)):
        evidence_map = {
            "technical": tech_ev, "direction": direction_ev,
            "experience": exp_ev, "culture": cul_ev, "location": loc_ev,
        }
        weighted = round(score * WEIGHTS[name], 1)
        dimensions[name] = {
            "score": score,
            "weight": WEIGHTS[name],
            "weighted": weighted,
            "evidence": evidence_map[name],
        }

    total, total_weighted = _aggregate(dimensions)
    verdict = verdict_for(total)

    if not (salary or "").strip():
        risks.insert(0, "薪资未标注")

    keyword_score, keyword_reason = score_jd(title, desc, cfg)

    hits: List[str] = []
    seen = set()
    for h in tech_ev:
        if h.startswith(("技能命中:", "加分词")):
            for part in h.split(":", 1)[1].split("(")[0].replace("+", "").split("/"):
                part = part.strip()
                if part and part not in seen:
                    seen.add(part)
                    hits.append(part)

    notes_parts = [profile["notes"]]
    notes_parts.append(f"关键词层得分={keyword_score}({keyword_reason})")
    notes_parts.append(f"加权原始值={total_weighted:.2f}")

    return {
        "total": total,
        "verdict": verdict,
        "dimensions": dimensions,
        "hits": hits[:10],
        "gaps": _find_gaps(title, desc, cfg),
        "risks": risks,
        "keyword_score": keyword_score,
        "notes": " | ".join(notes_parts),
    }


def format_report(result: dict) -> str:
    """人话报告卡（中文），CLI 展示用。"""
    lines = []
    v = result["verdict"]
    emoji = {"strong_apply": "🚀", "apply": "✅", "consider": "🤔", "skip": "🛑"}.get(v, "")
    lines.append("┌─ 岗位匹配报告 " + "─" * 34)
    lines.append(f"│ 总分: {result['total']}/100 → {emoji} {VERDICT_LABELS.get(v, v)} ({v})")
    lines.append("├─ 五维明细 " + "─" * 35)
    for name, d in result["dimensions"].items():
        bar = "█" * (d["score"] // 10) + "░" * (10 - d["score"] // 10)
        lines.append(
            f"│ {DIM_LABELS[name]} {bar} {d['score']:3d} ×{d['weight']:.0%} → {d['weighted']:5.1f}"
            f"   {'; '.join(d['evidence'])}"
        )
    if result.get("hits"):
        lines.append("├─ 技能命中: " + "、".join(result["hits"]))
    if result.get("gaps"):
        lines.append("├─ 相关缺口: " + "、".join(result["gaps"]))
    if result.get("risks"):
        lines.append("├─ ⚠️ 风险提示: " + "；".join(result["risks"]))
    lines.append("└─ 备注: " + result.get("notes", ""))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="五维岗位匹配引擎 CLI")
    parser.add_argument("title", help="岗位标题")
    parser.add_argument("--desc", default="", help="JD 正文")
    parser.add_argument("--desc-file", dest="desc_file", default="", help="从文件读 JD 正文")
    parser.add_argument("--company", default="", help="公司名")
    parser.add_argument("--salary", default="", help="薪资，如 15-25K")
    parser.add_argument("--city", default="深圳", help="城市")
    parser.add_argument("--config", default="", help="自定义 config.json 路径")
    parser.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    args = parser.parse_args()

    cfg = None
    if args.config:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))

    desc = args.desc
    if args.desc_file:
        desc = Path(args.desc_file).read_text(encoding="utf-8")

    result = explain_match(args.title, desc, company=args.company,
                           salary=args.salary, city=args.city, cfg=cfg)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

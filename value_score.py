#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RULES_v2.0 第三层：岗位价值评分器 VSCORE v1.1 (2026-08-31 定稿)。

定位：资格裁决器(job_decision)只回答「能不能投」；本模块回答「最值得投什么」——
把薪资/AI匹配度/制度/稳定性/成长/福利/地区可达性 合成一个可排序价值分。

铁律：
- 纯确定性规则，零 LLM（模型/解析层负责提供结构化信号，代码负责算分）
- 只排序不拦截：REJECT 永远归 job_decision，本模块输出 HIGH/NORMAL/LOW 投递优先级
- 地区永远不是硬门槛（拦截归 L1/L2），只是「值得消耗一个额度的程度」

v1.0→v1.1（2026-08-31 地区升级定稿）：
- 城市5 → 地区可达性15（S深圳15/A广州13/B杭宁蓉8/C其他3，异地接受+3、仅限本地-10）
- 薪资30→25 且 >=10K 内部细分（15K 压过 10K）；强度并入制度15；稳定/成长 10→8；福利 5→4

维度权重（合计 100）：
  薪资 25 | AI匹配度 25 | 工作制度 15 | 地区可达性 15 | 稳定性 8 | 技术成长 8 | 福利 4

权重版本化协议（2026-08-31 定稿，防「感觉式调权重」）：
  - VSCORE_VERSION 是唯一版本标识；改任何权重 → 版本 +0.1
  - 每版必须跑 scripts/vscore_benchmark.py 输出基准岗位得分表
  - 对比上一版基线后，变化必须是人话可解释的，不许拍脑袋
  - 基准岗位集 tests/benchmark_roles.json 不可删除
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

VSCORE_VERSION = "1.1"   # 2026-08-31 v1.1：城市5 → 地区可达性15（relocation 四档+异地信号）

# 权重表（只读约定：排序用，永不参与 ALLOW/REJECT —— 见 tests/test_architecture.py）
# v1.1（2026-08-31 地区升级定稿）：城市5 → 地区可达性15；薪资30→25；强度并入制度15。
# 合计 25+25+15+8+8+4+15 = 100（tests 锁死）。
WEIGHTS = {
    "salary": 25,        # 薪资带分
    "ai_match": 25,      # AI匹配度（复用 explain_match 技术+方向）
    "workday": 15,       # 工作制度（双休信号；v1.1 起并入原"强度"维度）
    "stability": 8,      # 稳定性（编制/国企/央企/上市）
    "growth": 8,         # 技术成长（AI核心/实施/弱AI）
    "location": 15,      # 地区可达性 v1.1（S深圳15/A广州13/B杭宁蓉8/C其他3 ±异地信号）
    "welfare": 4,        # 福利（五险一金/公积金）
}

# ── 薪资分（K/月，与 job_decision 分层对齐；v1.1 满分 25，10K+ 内部再细分：
#    用户定稿示例要求 杭州15K 必须排到 杭州10K 之前，一刀切25分会让两者同权） ──
SALARY_SCORE = {">=20K": 25, ">=15K": 23, ">=10K": 20,
                "8-10K": 15, "5-8K": 11, "<5K": 6, "unknown": 10}

# ── 稳定性信号 ──
STABILITY_HIGH = ["编制", "国企", "央企", "事业单位", "事业编", "正式工", "公务员",
                  "南方电网", "国家电网", "中石油", "中石化", "铁路局", "烟草",
                  "上市公司", "世界500强", "A股上市", "美股上市"]
STABILITY_MID = ["成立10年", "成立十年", "融资", "C轮", "D轮", "独角兽", "头部", "行业龙头"]

# ── 技术成长信号（AI 职责强度，与 v4.0 AI-1~5 对齐） ──
AI_CORE = ["agent", "智能体", "rag", "知识库", "大模型应用", "llm", "api集成",
           "模型调用", "提示词", "prompt", "工作流开发", "ai应用开发", "应用开发"]
AI_IMPL = ["ai实施", "ai解决方案", "企业ai", "落地", "部署", "接入", "集成", "自动化",
           "rpa", "dify", "coze", "fastgpt", "数字员工"]
AI_WEAK = ["ai", "大模型", "gpt", "deepseek", "chatgpt"]

# ── 福利信号 ──
WELFARE_HIGH = ["五险一金", "六险一金", "六险二金", "公积金", "补充医疗保险", "年终奖", "13薪", "14薪"]
WELFARE_MID = ["餐补", "包吃", "包住", "住宿", "交通补贴", "带薪年假", "双休"]

# ── 工作强度信号 ──
STRENGTH_GOOD = ["不加班", "加班少", "准点", "到点走", "弹性不打卡", "周末双休", "很少加班"]
STRENGTH_BAD = ["加班多", "经常加班", "大小周", "单休", "996", "每周休1天", "月休4天"]


@dataclass
class ValueScore:
    score: int                     # 0-100
    tier: str                      # HIGH >=80 / NORMAL 60-79 / LOW <60
    breakdown: Dict[str, int] = field(default_factory=dict)
    signals: List[str] = field(default_factory=list)

    def __str__(self):
        return f"价值{self.score}分[{self.tier}]"


def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


def _count_signal(text: str, words) -> int:
    return sum(1 for w in words if w in text)


def value_score(company: str, title: str, desc: str, salary: str,
                city: str = "", decision=None, match_result=None,
                salary_band: str = "unknown",
                salary_low_k: Optional[float] = None) -> ValueScore:
    """综合价值评分。decision=job_decision.Decision; match_result=explain_match 结果。

    任何一步失败都降级为「基础分」，绝不抛异常阻断投递。
    """
    title = title or ""
    desc = desc or ""
    combined = title + " " + desc
    bd: Dict[str, int] = {}
    sig: List[str] = []

    # 1) 薪资（25）—— salary_band 优先；unknown 但能解析出数时兜底解析；
    #    v1.1：>=10K 按实际下限细分 20/23/25（15K 必须压过 10K，见 relocation 定稿示例）
    from job_decision import parse_salary_low
    _low = salary_low_k if salary_low_k is not None else parse_salary_low(salary)
    band = salary_band or "unknown"
    if band == "unknown" and _low > 0:
        band = ">=10K" if _low >= 10 else "8-10K" if _low >= 8 else "5-8K" if _low >= 5 else "<5K"
    if band == ">=10K":
        band = ">=20K" if _low >= 20 else ">=15K" if _low >= 15 else ">=10K"
    bd["薪资"] = SALARY_SCORE.get(band, 10)
    if band in (">=10K", ">=15K", ">=20K", "8-10K"):
        sig.append(f"薪资带:{band}({bd['薪资']}/25)")

    # 2) AI匹配度（25）—— 复用 explain_match 的 technical+direction 加权
    if match_result and match_result.get("dimensions"):
        dims = match_result["dimensions"]
        tech = dims.get("technical", {}).get("weighted", 0) or 0
        dire = dims.get("direction", {}).get("weighted", 0) or 0
        bd["AI匹配度"] = min(25, round((tech + dire) * 25 / 60))
    else:
        if _has_any(combined.lower(), [w.lower() for w in AI_CORE]):
            bd["AI匹配度"] = 20
        elif _has_any(combined.lower(), [w.lower() for w in AI_IMPL]):
            bd["AI匹配度"] = 16
        elif _has_any(combined.lower(), [w.lower() for w in AI_WEAK]):
            bd["AI匹配度"] = 10
        else:
            bd["AI匹配度"] = 6
    if bd["AI匹配度"] >= 20:
        sig.append(f"AI核心职责({bd['AI匹配度']}/25)")

    # 3) 工作制度（15，v1.1 起并入原"强度"维度）—— 结构化信号优先，关键词兜底
    if _has_any(combined, ["双休", "周末双休", "做五休二", "上五休二", "正常双休"]):
        bd["制度"] = 15 if not _has_any(combined, STRENGTH_BAD) else 11
        sig.append("双休")
    elif _has_any(combined, STRENGTH_BAD):
        bd["制度"] = 5
        sig.append("制度未明示")
    else:
        bd["制度"] = 10

    # 4) 稳定性（8）
    if _has_any(combined, STABILITY_HIGH):
        bd["稳定性"] = 8
        sig.append("高稳定主体")
    elif _has_any(combined, STABILITY_MID):
        bd["稳定性"] = 6
    elif decision is not None and getattr(decision, "special_approval", False):
        bd["稳定性"] = 7  # 特批通道必有稳定信号
    else:
        bd["稳定性"] = 4

    # 5) 技术成长（8）
    c = combined.lower()
    if _has_any(c, [w.lower() for w in AI_CORE]):
        bd["成长"] = 8
    elif _has_any(c, [w.lower() for w in AI_IMPL]):
        bd["成长"] = 6
    elif _has_any(c, [w.lower() for w in AI_WEAK]):
        bd["成长"] = 5
    else:
        bd["成长"] = 2

    # 6) 福利（4）
    if _has_any(combined, WELFARE_HIGH):
        bd["福利"] = 4
    elif _has_any(combined, WELFARE_MID):
        bd["福利"] = 2
    else:
        bd["福利"] = 1

    # 7) 地区可达性（15）—— v1.1：城市是 HR 的第一道筛，不是普通加分
    # 深圳15 广州13 杭宁蓉8 其他3；明示接受异地+3；明示仅限本地-10（S档母城豁免）
    try:
        from relocation import evaluate_location as _loc_eval
        _le = _loc_eval(city, combined)
        bd["地区可达性"] = _le.score
        if _le.signals:
            sig.append(f"地区{_le.tier}:{';'.join(_le.signals)}")
        elif _le.tier != "S":
            sig.append(f"地区{_le.tier}({bd['地区可达性']}/15)")
    except Exception:
        bd["地区可达性"] = 8   # 降级中位分，绝不因地区评估异常阻断投递

    total = sum(bd.values())
    tier = "HIGH" if total >= 80 else "NORMAL" if total >= 60 else "LOW"
    return ValueScore(score=total, tier=tier, breakdown=bd, signals=sig)


if __name__ == "__main__":
    from pathlib import Path
    import json
    from job_decision import evaluate_job
    from match_engine import explain_match, load_candidate_profile

    cfg = {}
    if Path("config.json").exists():
        cfg = json.loads(Path("config.json").read_text())
    profile = load_candidate_profile(cfg)

    cases = [
        ("某科技", "AI应用工程师", "负责RAG知识库,Agent工作流,双休,五险一金", "12-20K", "深圳"),
        ("某科技", "AI应用工程师", "RAG知识库开发,双休,五险一金", "8.5-12K", "深圳"),
        ("南方电网", "数据运维值班员", "正式编制,五险一金齐全,稳定,双休", "5-7K", "广州"),
        ("某外包", "测试驻场", "华为驻场", "12-16K", "深圳"),
        ("某公司", "数据标注员", "纯标注作业", "4-6K", "成都"),
        ("某Agent科技", "Agent应用工程师", "Agent工作流开发,双休", "面议", "成都"),
        ("某公司", "客服专员", "客户服务", "8-10K", "北京"),
    ]
    for c, t, d, s, city in cases:
        dec = evaluate_job(c, t, d, s, city=city, cfg=cfg)
        if dec.action != "ALLOW":  # L2 判决展示（架构上 REJECT 只属于 job_decision）
            print(f"{t[:18]:20s} | {s:10s} | {str(dec):55s} → 不投")
            continue
        mr = explain_match(t, d, company=c, salary=s, city=city, cfg=cfg)
        vs = value_score(c, t, d, s, city=city, decision=dec, match_result=mr, salary_band=dec.salary_band)
        print(f"{t[:18]:20s} | {s:10s} | {str(vs):16s} | {json.dumps(vs.breakdown, ensure_ascii=False)} | {';'.join(vs.signals)}")
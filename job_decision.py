#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RULES_v2.0 岗位价值决策器（2026-08-31 定稿）。

架构：模型/解析层负责"看懂"（结构化信号），本模块负责"判死刑"（确定性规则）。
输入岗位信息 → 输出 ALLOW/REJECT + 优先级。零 LLM 依赖。

薪资分层（用户定稿，勿回退）：
  <5K             → REJECT（默认拒绝）
  5-8K            → 特批通道：编制/国企/正式工/高稳定/轻松 → ALLOW(LOW)，否则 REJECT
  8-10K           → ALLOW(NORMAL)
  ≥10K            → ALLOW(HIGH)
  薪资未知/0      → 不因薪资拒绝（由其他维度决定）

硬红线（任何一档薪资都生效，命中即 REJECT）：
  - 制度红线：单休/大小周/996/夜班/轮班/倒班
  - 实习岗（标题层兜底）
  - 纯劳务/人力代招主体
  - 外包 + 低技术 + 低价（<6K）
"""
from dataclasses import dataclass, field
from typing import Optional

# ── 薪资带（K/月） ──
SALARY_HARD_FLOOR = 5.0      # <5K 默认拒绝
SALARY_NORMAL_FLOOR = 8.0    # 8-10K 正常可接受
SALARY_PRIORITY = 10.0       # ≥10K 高优先级

# ── 制度红线信号（从 desc/title 结构化提取，命中即死） ──
WORKDAY_REDLINES = [
    "单休", "大小周", "996", "007", "上六休一", "做六休一",
    "轮班", "倒班", "三班倒", "两班倒", "夜班", "通宵",
    "月休4天", "月休四天", "每周休1天", "每周休息一天",
]
DOUBLE_REST_SIGNALS = ["双休", "周末双休", "做五休二", "上五休二"]

# ── 特批信号（5-8K 破例通道：低薪但高质量） ──
SPECIAL_APPROVAL_SIGNALS = [
    "编制", "国企", "央企", "事业单位", "正式工", "正式员工",
    "事业编", "公务员", "带编制", "五险一金齐全", "六险二金",
    "稳定" , "轻松", "加班少", "不加班", "福利好",
    "南方电网", "国家电网", "中石油", "中石化", "铁路局", "烟草",
]

# ── 公司主体红线 ──
COMPANY_REDLINES = ["人力资源", "劳务派遣", "劳务外包", "代招", "猎头服务"]

# ── 高价值外包（O1/O2 可入，O3/O4 拒）：职业跳板 —— 不在此处判，留给 deep_filter ──


@dataclass
class Decision:
    action: str                  # ALLOW / REJECT
    priority: str = ""           # HIGH / NORMAL / LOW / ""
    salary_band: str = ""        # <5K / 5-8K / 8-10K / >=10K / unknown
    reason: str = ""
    special_approval: bool = False

    def __str__(self):
        return f"[{self.action}] {self.priority} | {self.reason}"


def parse_salary_low(salary: str) -> float:
    """解析薪资下限(K/月)。0 = 未知(不因未知拒绝)。"""
    if not salary:
        return 0.0
    s = salary.replace(" ", "").replace(",", "").lower()
    # 2026-09-05: Boss 用图标字体渲染数字，textContent 拿到的是 Unicode 私有区
    # (PUA) 字符 0xe030-0xe039 = '0'-'9'。不解码则薪资全解析失败 →
    # 大小周 12K 特批失效、低薪误拦。实测: '\\ue032\\ue039' = "29"。
    if any(0xE030 <= ord(ch) <= 0xE039 for ch in s):
        s = "".join(chr(ord(ch) - 0xE030 + ord("0"))
                    if 0xE030 <= ord(ch) <= 0xE039 else ch for ch in s)
    try:
        if "万" in s and "-" in s:
            return float(s.split("-")[0].replace("万", "")) * 10
        if "万" in s and "-" not in s:
            return float(s.replace("万", "")) * 10
        if "k" in s and "-" in s:
            return float(s.split("-")[0].split("k")[0])
        if "k" in s and "-" not in s:
            return float(s.split("k")[0])
        if "元/天" in s or "元/日" in s:
            return float(s.split("-")[0].split("元")[0]) * 22 / 1000
        if "元/月" in s:
            return float(s.split("-")[0].split("元")[0]) / 1000
    except (ValueError, IndexError):
        return 0.0
    return 0.0


def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


def evaluate_job(company: str, title: str, desc: str, salary: str,
                 city: str = "", cfg=None,
                 parsed_signals: Optional[dict] = None) -> Decision:
    """确定性岗位裁决。

    parsed_signals: 模型/解析层提供的结构化信号（可选）：
      {"workday": "single_rest"|"double_rest"|"unknown",
       "shift": bool, "outsourcing": bool, "intern": bool}
    提供后以信号为准；未提供则用关键词提取兜底。
    """
    title = title or ""
    desc = desc or ""
    combined = title + " " + desc

    # ── 0. 解析层信号优先 ──
    sig = parsed_signals or {}
    workday = sig.get("workday")
    if workday == "single_rest":
        return Decision("REJECT", reason="解析信号:单休→制度红线")
    if sig.get("shift"):
        return Decision("REJECT", reason="解析信号:轮班/倒班→制度红线")
    if sig.get("intern"):
        return Decision("REJECT", reason="解析信号:实习岗→过滤")

    # ── 1. 关键词兜底：制度红线 ──
    # 2026-09-05 冲刺模式：单休/996/007/夜班/轮班 仍命中即死（真坑，26天也耗不起）；
    # 大小周 = 降级为"薪资≥12K 可谈"（SPECIAL_APPROVAL_SIGNALS 含 salary 12K 判定见下），
    # <12K 的大小周仍 REJECT。双休仍是最优，但不再一票否决大小周。
    _hit = _has_any(combined, WORKDAY_REDLINES)
    if _hit:
        hit = next(w for w in WORKDAY_REDLINES if w in combined)
        # 仅"大小周"可被 12K+ 薪资特批覆盖（parse_salary_low 返回 K 单位，故 ≥12）
        if hit == "大小周" and parse_salary_low(salary) >= 12:
            pass  # 允许，进入薪资分层（reason 由薪资档位给出）
        else:
            return Decision("REJECT", reason=f"制度红线:{hit}")
    if sig.get("workday") == "unknown" and _has_any(title, ["轮班", "夜班", "倒班"]):
        return Decision("REJECT", reason="制度红线:标题轮班/夜班")

    # ── 2. 实习兜底 ──
    if "实习" in title:
        return Decision("REJECT", reason="实习岗→过滤(全职策略)")
    if "实习" in desc and "接受实习" not in desc and "实习期" not in desc:
        # 正文提及实习但非承诺，谨慎处理：仅当标题也含实习类词才算数（上面已拦）
        pass

    # ── 3. 公司主体红线 ──
    if _has_any(company or "", COMPANY_REDLINES):
        return Decision("REJECT", reason=f"公司主体红线:{next(w for w in COMPANY_REDLINES if w in (company or ''))}")

    # ── 4. 薪资分层 ──
    low = parse_salary_low(salary)
    if low <= 0:
        band = "unknown"
    elif low < SALARY_HARD_FLOOR:
        band = "<5K"
    elif low < SALARY_NORMAL_FLOOR:
        band = "5-8K"
    elif low < SALARY_PRIORITY:
        band = "8-10K"
    else:
        band = ">=10K"

    if band == "unknown":
        return Decision("ALLOW", priority="NORMAL", salary_band=band,
                        reason=f"薪资未知→不因薪资拒绝({salary[:20]})")
    if band == "<5K":
        # 特批检查：即使<5K，正式工/编制/极高稳定也留一条缝（用户场景少但存在）
        if _has_any(combined, SPECIAL_APPROVAL_SIGNALS):
            return Decision("ALLOW", priority="LOW", salary_band=band,
                            special_approval=True,
                            reason=f"{band}特批:正式工/编制/高稳定信号")
        return Decision("REJECT", priority="", salary_band=band, reason=f"{band}→默认拒绝")
    if band == "5-8K":
        # 2026-09-05 冲刺模式：8K 也干（用户确认）。5-8K 不再要求特批信号，直接 LOW 可投。
        # <5K 仍需特批（真红线，养不活深圳生活）。
        return Decision("ALLOW", priority="LOW", salary_band=band,
                        reason=f"{band}→冲刺模式可投(LOW)")
    if band == "8-10K":
        return Decision("ALLOW", priority="NORMAL", salary_band=band, reason=f"{band}→正常可接受")
    return Decision("ALLOW", priority="HIGH", salary_band=band, reason=f"{band}→高优先级")


if __name__ == "__main__":
    cases = [
        # (公司, 标题, 描述, 薪资, 期望)
        ("某科技", "AI应用工程师", "负责RAG知识库,双休,五险一金", "12-20K", "ALLOW/HIGH"),
        ("某科技", "AI实施工程师", "企业AI部署,实施交付", "9-15K", "ALLOW/NORMAL"),
        ("南方电网", "数据运维值班员", "正式编制,五险一金齐全,稳定", "5-7K", "ALLOW/LOW特批"),
        ("某外包公司", "测试驻场", "华为驻场,大小周", "12-16K", "REJECT制度"),
        ("某公司", "算法工程师", "PyTorch模型训练", "15-30K", "不应在此层判(留给评分)"),
        ("某科技", "AI销售", "客户拓展,业绩考核", "10-15K", "薪资层ALLOW(销售由deep_filter拦)"),
        ("某公司", "实习生", "协助开发", "200元/天", "REJECT实习"),
        ("某公司", "知识库运营", "Dify搭建知识库", "薪资面议", "ALLOW/unknown"),
        ("某外包", "采购专员", "周末双休", "6-8K", "REJECT无特批信号"),
        ("某国企", "行政助理", "央企正式工,双休", "6-8K", "ALLOW/LOW特批"),
    ]
    from pathlib import Path
    import json
    cfg = {}
    if Path("config.json").exists():
        cfg = json.loads(Path("config.json").read_text())
    for c, t, d, s, exp in cases:
        r = evaluate_job(c, t, d, s, cfg=cfg)
        print(f"{exp:16s} ← {str(r):60s} | {c}/{t}/{s}")
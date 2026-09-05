#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""semantic_parser.py — L3 语义解析层·确定性第一层（v5.2，2026-08-31）。

解决关键词系统已到瓶颈的问题：岗位标题 ≠ 实际岗位类型。
两个实测漏网案例（黄金回归集，永不复漏）：
    "AI+客户服务顾问｜平安康养财富｜金融顾问"  → 实际金融销售 → HARD_BLOCK
    "AI 影视视频评测"                        → 实际数据标注 → HARD_BLOCK

架构原则（用户定稿）：
    LLM = 不确定性核心；Schema = 笼子；确定性规则 = 最终裁判。
    本层是纯确定性信号抽取（无 LLM 依赖，零成本、零漂移）；
    LLM 语义理解层后续挂到 parse_with_llm() 接口，输出同样必须过 Schema 校验，
    校验失败 → 重试/换供应商 → 仍失败 → UNKNOWN，绝不把坏数据往下传（R-EXT-3）。

输出契约（与 job_schema.JobRecord 对齐）：
    {
      "category": enum,           # 只允许枚举值
      "primary_function": enum,   # AI_CORE/AI_DELIVERY/SALES/CUSTOMER_SERVICE/
                                  # ANNOTATION/OTHER/UNKNOWN
      "ai_core": bool,
      "sales": bool,
      "customer_service": bool,
      "annotation": bool,
      "verdict": "PASS" | "HARD_BLOCK" | "UNKNOWN",
      "block_reason": str,        # HARD_BLOCK 时的人话理由
      "evidence": [str],          # 每个判断的原文依据（R-EXT-4 面试可辩护同源）
      "confidence": 0-1,          # 确定性层：命中信号数归一
    }
"""
from typing import Any, Dict, List, Optional

PARSER_VERSION = "0.1"

CATEGORY_ENUM = {
    "AI_APPLICATION", "AI_DELIVERY", "AI_OPS", "AUTOMATION",
    "DIGITAL", "TESTING", "SUPPORT", "SALES_DISGUISED",
    "ANNOTATION_DISGUISED", "OTHER", "UNKNOWN",
}
FUNCTION_ENUM = {
    "AI_CORE", "AI_DELIVERY", "SALES", "CUSTOMER_SERVICE",
    "ANNOTATION", "OTHER", "UNKNOWN",
}

# ── 确定性信号表（每条都来自真实漏网/拦截案例，加词必须有案例依据，防规则肥大）──

# 销售伪装：标题词 + JD 词两级证据
SALES_TITLE_SIGNS = [
    "金融顾问", "财富顾问", "保险顾问", "康养财富", "理财顾问",
    "贷款顾问", "信用卡销售", "电销", "电话销售",
]
SALES_JD_SIGNS = [
    "客户开发", "业绩指标", "销售转化", "产品推广", "客户转化",
    "电话沟通", "陌拜", "佣金", "提成",
]

# 客服伪装：注意"顾问"单独出现不触发（AI解决方案顾问是合法目标岗）
SERVICE_TITLE_SIGNS = ["客户服务", "客服", "热线", "售后支持", "投诉处理", "呼叫中心"]

# 技术岗豁免：标题是纯技术岗（工程师/开发/技术/研发/架构/运维）时，JD 里的
# 销售信号（客户开发/产品推广/电话沟通等）多为"需求对接"，不是销售岗。
# 2026-09-05 误杀复盘：华为AI软件工程师/庭宇AI产品技术经理/海博拓天AI产品运营
# 被 JD 销售词≥2 误拦。技术岗豁免后，仅标题销售词仍可触发（真销售标题）。
TECH_ROLE_SIGNS = ["工程师", "开发", "技术", "研发", "架构", "运维",
                   "算法", "程序员", "专家"]  # 注意不含"经理"（客户经理/销售经理不豁免）
# 2026-09-05 追加：产品经理/产品岗 ≠ 销售（AI产品经理/TL 被JD销售词误拦案）。
# 只豁免"产品"，"销售经理/客户经理/BD"仍会被 JD 销售词拦。
PRODUCT_ROLE_SIGNS = ["产品", "产品经理", "产品运营"]

# 标注伪装·强信号：命中即可否决（override 词可取消，交 L2）
ANNOTATION_STRONG_SIGNS = [
    "标注", "数据标注", "打标", "RLHF标注", "数据清洗", "语料",
    "视频评测", "影视视频评测", "评测与标注", "评测标注",
]
# 弱信号：单独命中永不通否决（BOUNDARY 词，去留交 L2/排序层）。
# "AI训练师"可能合法（模型调优）也可能是标厂，确定性层没能力区分——
# 区分不了就不杀，这正是防误杀的边界（用户 2026-08-31 定稿三类分类法）。
ANNOTATION_WEAK_SIGNS = ["训练师"]

# AI 核心岗正向信号（对抗"全是 AI 字样"的噪音）
AI_CORE_SIGNS = [
    "rag", "agent开发", "agent 开发", "workflow", "工作流", "dify",
    "langchain", "知识库", "智能体", "大模型应用", "llm应用",
    "实施", "部署", "交付", "推理优化", "微调", "aigc应用",
]

# 合法豁免（语义层的"防误杀"机制，2026-08-31 用户定稿语义）：
# 命中伪装词但同时命中以下词时，仅"取消语义层直接否决"，继续进 L2 资格层裁决——
# 不是直接 PASS！防"AI评测工程师实际还是数据标注"被豁免词放过（用户点名的风险）。
# 这类案例的最终去留由 L2/后续 LLM 语义层决定，golden test 里锁死此行为。
LEGITIMATE_OVERRIDE = ["评测工程师", "测试工程师", "算法", "开发", "架构"]


def _has(text: str, signs: List[str]) -> List[str]:
    t = (text or "").lower()
    return [s for s in signs if s.lower() in t]


def parse(title: str, jd_text: str = "", company: str = "") -> Dict[str, Any]:
    """确定性语义解析。纯函数：同输入必同输出，无外部依赖。"""
    title = title or ""
    jd = jd_text or ""
    combined = f"{title} {jd}"
    evidence: List[str] = []

    sales_title = _has(title, SALES_TITLE_SIGNS)
    sales_jd = _has(jd, SALES_JD_SIGNS)
    service = _has(title, SERVICE_TITLE_SIGNS)
    annotation = _has(title, ANNOTATION_STRONG_SIGNS)
    annotation_weak = _has(title, ANNOTATION_WEAK_SIGNS)
    ai_core = _has(combined, AI_CORE_SIGNS)
    override = _has(title, LEGITIMATE_OVERRIDE)

    is_sales = bool(sales_title)
    # 2026-09-05：技术岗/产品岗豁免——标题含 工程师/开发/技术/产品 等词时，JD 销售信号
    # 不算数（需求对接≠销售岗）。仅当标题本身无技术/产品属性且 JD 销售词≥2 才判销售。
    if not is_sales and len(sales_jd) >= 2 and not _has(title, TECH_ROLE_SIGNS) \
            and not _has(title, PRODUCT_ROLE_SIGNS):
        is_sales = True
    is_service = bool(service)
    is_annotation = bool(annotation) and not override  # 弱信号永不触发

    if sales_title:
        evidence.append(f"标题含销售伪装词: {','.join(sales_title)}")
    if len(sales_jd) >= 2:
        evidence.append(f"JD含销售信号≥2: {','.join(sales_jd[:4])}")
    if service:
        evidence.append(f"标题含客服信号: {','.join(service)}")
    if is_annotation:
        evidence.append(f"标题含标注信号: {','.join(annotation)}")
    if ai_core:
        evidence.append(f"AI核心信号: {','.join(ai_core[:4])}")

    # ── 判定（顺序即优先级：伪装识别 > 核心识别）──
    if is_sales:
        verdict, func, cat = "HARD_BLOCK", "SALES", "SALES_DISGUISED"
        reason = "实际为销售/金融顾问岗（标题或JD证据），非目标序列"
    elif is_service:
        verdict, func, cat = "HARD_BLOCK", "CUSTOMER_SERVICE", "SALES_DISGUISED"
        reason = "实际为客服岗（'AI+客户服务'类伪装），非目标序列"
    elif is_annotation:
        verdict, func, cat = "HARD_BLOCK", "ANNOTATION", "ANNOTATION_DISGUISED"
        reason = "实际为数据标注/评测流水线岗，非目标序列"
    elif ai_core:
        verdict, func, cat = "PASS", "AI_CORE", "AI_APPLICATION"
        reason = ""
    else:
        verdict, func, cat = "UNKNOWN", "UNKNOWN", "UNKNOWN"
        reason = ""

    hits = len(sales_title) + len(sales_jd) + len(service) + len(annotation) + len(ai_core)
    return {
        "parser_version": PARSER_VERSION,
        "category": cat,
        "primary_function": func,
        "ai_core": bool(ai_core),
        "sales": is_sales,
        "customer_service": is_service,
        "annotation": is_annotation,
        "verdict": verdict,
        "block_reason": reason,
        "evidence": evidence,
        "confidence": min(1.0, hits / 3.0),
    }


def validate_parse_output(out: Dict[str, Any]) -> bool:
    """R-EXT-3：LLM 输出必须过 Schema 校验。校验失败 = 坏数据，禁止下传。"""
    if not isinstance(out, dict):
        return False
    if out.get("category") not in CATEGORY_ENUM:
        return False
    if out.get("primary_function") not in FUNCTION_ENUM:
        return False
    if out.get("verdict") not in {"PASS", "HARD_BLOCK", "UNKNOWN"}:
        return False
    for k in ("ai_core", "sales", "customer_service", "annotation"):
        if not isinstance(out.get(k), bool):
            return False
    if not isinstance(out.get("evidence"), list):
        return False
    return True


def parse_with_schema_guard(title: str, jd_text: str = "", company: str = "") -> Dict[str, Any]:
    """带 Schema 闸门的解析入口：任何来源（确定性/未来 LLM）的输出都过校验。

    校验失败 → 返回 UNKNOWN 结构（不是抛异常、不是放行）：
    绝不能把坏数据当正常数据继续往下跑（用户定稿）。
    """
    try:
        out = parse(title, jd_text, company)
    except Exception:
        out = {}
    if not validate_parse_output(out):
        return {
            "parser_version": PARSER_VERSION, "category": "UNKNOWN",
            "primary_function": "UNKNOWN", "ai_core": False, "sales": False,
            "customer_service": False, "annotation": False,
            "verdict": "UNKNOWN", "block_reason": "Schema校验失败，数据不可用",
            "evidence": [], "confidence": 0.0,
        }
    return out


def gate(title: str, jd_text: str = "", company: str = "") -> Optional[str]:
    """给主流程的硬门：返回拦截理由（str）或 None（放行）。

    UNKNOWN 不拦截 —— 语义层只负责识别伪装，资格判断归 L2（防 L3 越权重蹈覆辙）。
    """
    out = parse_with_schema_guard(title, jd_text, company)
    if out["verdict"] == "HARD_BLOCK":
        return out["block_reason"]
    return None

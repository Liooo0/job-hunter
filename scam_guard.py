#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""招聘诈骗防线 — 招转培 / 入职前收费 / 垫付刷单（2026-09-20 新增）。

缘起：2026-09-20 人工核一个「远程办公 AI 训练师」岗（成都 / 10-15K / 主体为
个人独资工作室）时确认，这类岗位的判据**不在标题，也不在公司名**：

  - 库里「远程 + 标注/训练师」命中 47 条，其中多数是**正经**居家标注岗
    （万声通讯、汉克时代、创焱智科、澳鹏…），按组合词拦会大面积误杀；
  - 「个人独资工作室」这种主体在工商侧是批量注册的，**换名比词表更新快**，
    按名字拉黑既拦不住又给人「已经防住了」的错觉。

真正可判的硬信号只有三类，且都写在 **JD 正文**里：

  1. 入职前收费（培训费 / 押金 / 设备费 / 软件授权费 / 建档费 / 服装费 …）
  2. 垫付·刷单·走账·拉人头（本身违法，与金额无关）
  3. 显式招转培（培训后上岗 / 包就业 / 签培训协议）

设计原则（2026-09-20 用户定稿）：
  - **只拦硬信号**：命中即 REJECT，与制度红线同级，任何薪资档都不例外。
  - **弱信号只标记不拦**：远程+自备设备、工作室主体、零基础+高薪话术 →
    进 `Decision.risk_flags` 供人工复核，**不参与裁决**。
  - **否定语境必须放行**：正规 JD 常写「不收取任何费用」「培训费公司承担」，
    这是加分项，不能被词表反过来杀掉。判定按**分句 + 邻近窗口**，
    绝不跨标点 —— 否则「无需经验，需缴纳培训费」会被误放行。

⚠️ 覆盖范围（2026-09-20 实测，别当成三平台通用）：
  三个平台都经过 `job_decision.evaluate_job`，但**只有 Boss 会传真实 desc**
  （`boss_apply.py:1126`）。51job / 猎聘 传的是**空串**
  （`platform_51job.py:350`、`platform_liepin.py:212`），
  所以那两个平台上只有**公司名/标题层**的字面命中有效，正文层扫不到。
  根因与已知的「jd_text 全库为空」是同一个（采集边界没落 JD 原文），
  **单独跟踪，不在本模块内修**。
"""

# ═══════════════════════════════════════════════════════════════
# 硬信号词表
# ═══════════════════════════════════════════════════════════════

# ── A. 入职前收费 ──
# 这些名词在「写给候选人看的 JD 正文」里几乎不会正当出现。
# 刻意**不收录**语义太宽的词（服务费 / 管理费 / 材料费 / 体检费 / 违约金）——
# 它们在正规 JD 里能出现，收进来就是误杀。
FEE_NOUNS = (
    "培训费", "培训费用", "学费", "课程费", "学习费", "教材费",
    "押金", "保证金", "设备押金", "岗位预留金", "诚意金", "定金",
    "报名费", "建档费", "入职费", "录用费", "服装费", "工牌费", "工装费",
    "设备费", "器材费", "软件授权费", "授权费", "账号费", "激活费",
    "中介费", "介绍费", "推荐费",
)

# ── B. 垫付 / 刷单 / 走账 / 拉人头（违法，与金额无关）──
ILLEGAL_WORDS = (
    "垫付", "垫资", "垫钱", "先行垫付",
    "刷单", "刷好评", "刷销量", "刷流水", "刷信誉",
    "走账", "过账", "代收货款", "跑分", "洗钱",
    "发展下线", "拉人头", "拉下线",
)

# ── C. 显式招转培 ──
TRAIN_TO_HIRE = (
    "培训后上岗", "培训后安排", "培训后方可", "先培训再上岗", "先培训后上岗",
    "培训包就业", "包就业", "包分配工作", "学完上岗", "学完推荐就业",
    "培训结业后入职", "需先参加培训", "参加培训后方可", "岗前培训费",
    "签培训协议",
)

# ── 否定语境词 ──
# 只放行「真的在承诺不收费」的写法。「无需经验，需缴纳培训费」跨了逗号，
# 不算否定 —— 这正是必须分句判的原因。
NEG_WORDS = (
    "无需", "不用", "不需", "不必", "不收", "不缴", "不收取", "不缴纳",
    "没有任何", "无任何", "免收", "免缴", "免费提供", "公司承担",
    "公司提供", "全额报销", "报销",
)
# 招转培专用的短否定前缀（"不包就业" / "非包就业"）
NEG_SHORT = ("不", "非", "无", "免", "别")

# 分句边界：跨过这些字符就不再是修饰关系
_CLAUSE_SEP = "，,。；;！!？?、\n\r\t "
_NEG_SPAN = 8        # 费用词左右各 8 字内找否定词


# ═══════════════════════════════════════════════════════════════
# 硬信号扫描
# ═══════════════════════════════════════════════════════════════

def _clause_of(text: str, idx: int) -> tuple[str, int]:
    """取 idx 所在分句的原文与该分句在 text 中的起点。"""
    start = idx
    while start > 0 and text[start - 1] not in _CLAUSE_SEP:
        start -= 1
    end = idx
    n = len(text)
    while end < n and text[end] not in _CLAUSE_SEP:
        end += 1
    return text[start:end], start


def _negated(text: str, idx: int, span: int = _NEG_SPAN,
             words=NEG_WORDS) -> bool:
    """词在 text[idx] 处，是否处于否定语境。

    **只在同一分句内、且相邻 span 字内**才算否定。右窗口留得更宽一点，
    因为「培训费**由公司承担**」这类承诺跟在词后面。
    """
    clause, start = _clause_of(text, idx)
    rel = idx - start
    window = clause[max(0, rel - span): rel + span + 6]
    return any(w in window for w in words)


def _hits(text: str, words, skip_negated: bool = True,
          neg_words=NEG_WORDS, span: int = _NEG_SPAN) -> list:
    """返回命中的词（去重、保序）。skip_negated=True 时跳过否定语境里的命中。"""
    out = []
    for w in words:
        pos = text.find(w)
        while pos != -1:
            if not (skip_negated and _negated(text, pos, span, neg_words)):
                out.append(w)
                break
            pos = text.find(w, pos + 1)
    return out


def detect_scam(company: str, title: str, desc: str) -> tuple[bool, str]:
    """硬信号扫描。命中即 REJECT —— 与制度红线同级，任何薪资档都不例外。

    返回 `(是否拦截, 原因)`。原因形如 `诈骗红线:入职前收费(培训费)`，
    与 `制度红线:` / `文化红线:` / `公司主体红线:` 保持同一命名族，便于 grep 对账。

    三者优先级：收费 > 违法 > 招转培（收费最常见，也是最先该被看见的）。
    """
    text = f"{company or ''} {title or ''} {desc or ''}".lower()

    hit = _hits(text, FEE_NOUNS)
    if hit:
        return True, f"诈骗红线:入职前收费({hit[0]})"

    hit = _hits(text, ILLEGAL_WORDS)
    if hit:
        return True, f"诈骗红线:垫付/刷单({hit[0]})"

    hit = _hits(text, TRAIN_TO_HIRE, neg_words=NEG_WORDS + NEG_SHORT, span=4)
    if hit:
        return True, f"诈骗红线:招转培({hit[0]})"

    return False, ""


# ═══════════════════════════════════════════════════════════════
# 弱信号标记（**不参与裁决**）
# ═══════════════════════════════════════════════════════════════

REMOTE_WORDS = ("远程", "居家", "线上", "在家", "云办公", "异地办公")
BYO_DEVICE_WORDS = ("自备", "自带设备", "自带电脑", "个人电脑", "自己的电脑",
                    "自行准备电脑", "备用电脑", "自购", "需自备")
SHELL_ENTITY_WORDS = ("工作室", "个人独资", "经营部", "服务部")
ANNOTATION_WORDS = ("标注", "训练师", "数据标注", "打标")
NO_EXP_WORDS = ("无需经验", "无经验", "零基础", "小白", "包教包会",
                "接受无经验", "不限经验", "经验不限")
HYPE_WORDS = ("高薪", "月入", "轻松", "简单上手", "稳定收入", "躺赚", "日入")


def weak_flags(company: str, title: str, desc: str) -> list:
    """弱信号 —— **只标记，不参与裁决**。

    这些组合在正规岗位里同样常见，单拎出来不构成拦截理由。作用只有一个：
    让复核的人一眼看到风险面，决定要不要人工追一句、或者干脆放弃。

    返回短标签列表，直接挂到 `Decision.risk_flags`。
    """
    text = f"{company or ''} {title or ''} {desc or ''}".lower()
    comp = (company or "").lower()
    flags = []

    remote = any(w in text for w in REMOTE_WORDS)
    if remote and any(w in text for w in BYO_DEVICE_WORDS):
        flags.append("远程岗要求自备设备")
    if any(w in comp for w in SHELL_ENTITY_WORDS):
        flags.append("主体形态:工作室/个人独资")
    if remote and any(w in text for w in ANNOTATION_WORDS):
        flags.append("远程+标注/训练师")
    if any(w in text for w in NO_EXP_WORDS) and any(w in text for w in HYPE_WORDS):
        flags.append("话术:零基础+高薪")

    return flags

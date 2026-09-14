#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""岗位存活判定（MVP，2026-09-12）

移植自 career-ops-hq/career-ops 的 `liveness-core.mjs`（MIT），
模式表原样保留，另加中文平台（Boss/51job/猎聘）的闭站措辞。

── 为什么必须先归一化再匹配 ──
（career-ops 的真实踩坑）某招聘站把闭站提示渲染成 `Cette offre n'est plus disponible.`
用的是 U+2019 弯撇号，而模式里写的是 ASCII 撇号 → **静默永不匹配**
→ 明确过期的岗掉进"无 Apply 控件"分支 → 变成 uncertain → **永远过滤不掉**。
所以入口先做：弯引号→ASCII、NFD 去变音符、压缩空白，所有模式写成"归一化字母表"。

── 两条防误判守卫（都是血泪）──
1. `has been filled` 必须双向守卫：
   lookbehind 排除 "application/form has been filled"，lookahead 排除 "filled out"。
   否则会把一条**活岗**（文案里有"一旦申请表填写完成…"）读成已过期。
2. **反爬插页必须判"不确定"，绝不判"过期"**：
   Cloudflare/hCaptcha 挑战页（"Just a moment..." / "enable javascript and cookies" / cf-ray）
   内容极短又没有 Apply 控件，若不单独拦就会掉进"内容不足 → 过期"，
   于是把活岗写进历史 → **从此永久过滤掉**。所以识别出挑战页一律归 uncertain。
"""
import re
from typing import Optional, Tuple

# ── 判定结果 ──
ACTIVE = "active"           # 看起来是真实、可投的岗位页
EXPIRED = "expired"         # 明确闭站/已招满/已过期
LISTING = "listing_page"    # 拿到的是搜索结果页，不是岗位页
CHALLENGE = "challenge"     # 反爬插页 → 必须按「不确定」处理
INSUFFICIENT = "insufficient_content"  # 内容不足，无法判定 → 不确定

# 「不确定」集合：这些状态**绝不能**当作过期去写历史/拉黑
UNCERTAIN_STATES = (CHALLENGE, INSUFFICIENT)


def normalize_for_match(text: str) -> str:
    """弯引号→ASCII、去变音符(NFD)、压缩空白。所有模式在这个字母表上书写。"""
    if not isinstance(text, str):
        return ""
    s = text
    s = re.sub(r"[\u2018\u2019\u02bc\u2032\u00b4`]", "'", s)
    s = re.sub(r"[\u201c\u201d\u2033]", '"', s)
    # NFD 去组合变音符：expirée → expiree
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    s = re.sub(r"[\u0300-\u036f]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


# ── 明确过期/闭站（多语言）──
HARD_EXPIRED_PATTERNS = [
    # 英文
    # 广义的「已招满」单独处理（见 _filled_guard）：Python 的 re 不支持变长
    # lookbehind，所以这里只放简单形态，双向守卫在代码里做。
    r"job (is )?no longer available",
    r"job.*no longer open",
    r"this job has expired",
    r"job posting has expired",
    r"no longer accepting applications",
    r"this (position|role|job) (is )?no longer",
    r"this job (listing )?is closed",
    r"job (listing )?not found",
    r"the page you are looking for doesn't exist",
    r"applications?\s+(?:(?:have|are|is)\s+)?closed",
    r"closed on \d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    r"closed on (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}",
    # 德文
    r"diese stelle (ist )?(nicht mehr|bereits) besetzt",
    # 法文（去变音符后书写：expiree 可匹配 expirée）
    r"offre (expiree|n'est plus disponible)",
    r"(cette )?offre n'est plus (disponible|en ligne|active)",
    r"(offre|poste|annonce) (deja )?pourvu(e)?",
    r"offre (cloturee|desactivee|terminee)",
    r"ce poste n'est plus (disponible|a pourvoir|ouvert)",
    r"recrutement (termine|cloture)",
    r"candidatures (closes|cloturees)",
    # 中文平台（job-hunter 扩展，非 career-ops 原有）
    r"该?职位已?(过期|失效|下线|关闭|结束)",
    r"该?岗位已?(过期|失效|下线|关闭|结束|招满)",
    r"已?停止?招聘",
    r"招聘已?(结束|截止)",
    r"职位不存在",
    r"已招满",
    r"简历投递已?(结束|截止|关闭)",
    r"该职位已下线",
    r"很抱歉[，,].{0,12}(已|不再)",
]

# ── 拿到的是列表页而非岗位页 ──
LISTING_PAGE_PATTERNS = [
    r"\d+\s+jobs?\s+found",
    r"search for jobs page is loaded",
    r"\d+\s*个职位",
    r"共\s*\d+\s*个?岗位",
    r"搜索结果",
]

# ── 反爬插页：必须判「不确定」，绝不判「过期」──
BOT_CHALLENGE_PATTERNS = [
    r"just a moment",
    r"performing security verification",
    r"checking your browser before",
    r"verify you are (a |not a )?human",
    r"enable javascript and cookies to continue",
    r"attention required.*cloudflare",
    r"\bray id\b",
    r"\bcf-ray\b",
    # 中文平台风控/验证
    r"请完成安全验证",
    r"访问过于频繁",
    r"操作频繁",
    r"滑动验证",
    r"人机验证",
    r"请稍候",
]

# 页面出现这些 => 有实际的投递控件（Johnson & Johnson 用的 High 可靠性信号）
APPLY_SIGNALS = [
    r"立即申请", r"投递简历", r"申请职位", r"立即沟通", r"继续沟通",
    r"apply now", r"apply for this job", r"submit application", r"easy apply",
]

_EXPIRED_RE = [re.compile(p, re.I) for p in HARD_EXPIRED_PATTERNS]
_LISTING_RE = [re.compile(p, re.I) for p in LISTING_PAGE_PATTERNS]
_CHALLENGE_RE = [re.compile(p, re.I) for p in BOT_CHALLENGE_PATTERNS]
_APPLY_RE = [re.compile(p, re.I) for p in APPLY_SIGNALS]

# 「岗位 ... has been filled」——career-ops 里的双向守卫：
#   lookbehind 排除 "application/form has been filled"（填表，不是招满）
#   lookahead  排除 "filled out"（填表）
# Python 的 re 不支持变长 lookbehind，所以拆成捕获组 + 代码判定，语义完全一致。
_FILLED_RE = re.compile(
    r"\b(?:job|jobs|position|role|posting|opening|vacancy|requisition|req|listing)\b"
    r"([\s\S]{0,60}?)has been filled\b(\s+out)?", re.I)


def _filled_guard(text: str) -> bool:
    """『... has been filled』是否应判为已招满（双向守卫，防把活岗读成过期）。"""
    for m in _FILLED_RE.finditer(text):
        gap = m.group(1) or ""
        tail = (m.group(2) or "").strip().lower()
        if tail == "out":
            continue                                  # "filled out" = 填表
        if re.search(r"\b(?:application|form)\b[\s\S]{0,40}$", gap, re.I):
            continue                                  # "the application form has been filled"
        return True
    return False

MIN_CONTENT_CHARS = 200


def has_apply_control(text: str) -> bool:
    n = normalize_for_match(text)
    return any(p.search(n) for p in _APPLY_RE)


def classify(text: str, min_chars: int = MIN_CONTENT_CHARS) -> Tuple[str, str]:
    """判定一个岗位页的存活状态。返回 (state, 命中的模式/说明)。

    ⚠️ 调用方对所有 UNCERTAIN_STATES 一律按「不确定」处理：
       不写历史、不拉黑、不改状态，留待下次复核。
    """
    n = normalize_for_match(text or "")
    if not n.strip():
        return INSUFFICIENT, "空内容"

    # 1. 反爬插页优先（否则会掉进"内容不足→过期"，把活岗永久过滤掉）
    for p in _CHALLENGE_RE:
        if p.search(n):
            return CHALLENGE, f"反爬插页: {p.pattern[:40]}"

    # 2. 明确闭站（含「已招满」的双向守卫）
    if _filled_guard(n):
        return EXPIRED, "闭站措辞: ... has been filled（已排除 application/form 与 filled out）"
    for p in _EXPIRED_RE:
        if p.search(n):
            return EXPIRED, f"闭站措辞: {p.pattern[:48]}"

    # 3. 列表页
    for p in _LISTING_RE:
        if p.search(n):
            return LISTING, f"列表页特征: {p.pattern[:40]}"

    # 4. 内容不足 → 不确定（不是过期！）
    if len(n.strip()) < min_chars:
        return INSUFFICIENT, f"内容仅 {len(n.strip())} 字 < {min_chars}"

    return ACTIVE, "未命中任何闭站/挑战特征"


def should_filter_out(state: str) -> bool:
    """只有明确过期才允许写历史/拉黑。挑战页与内容不足一律不动。"""
    return state == EXPIRED


def describe(state: str) -> str:
    return {
        ACTIVE: "✅ 存活",
        EXPIRED: "⏹️ 已过期（可写历史/拉黑）",
        LISTING: "📋 列表页（不是岗位页，需重取）",
        CHALLENGE: "🛡️ 反爬插页（不确定，勿判过期）",
        INSUFFICIENT: "❔ 内容不足（不确定，勿判过期）",
    }.get(state, state)

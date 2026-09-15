#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四态溯源 + 硬数字闸（MVP，2026-09-12）

移植自 career-ops-hq/career-ops 的 `story-provenance-check.mjs`（MIT），
按 job-hunter 的数据形态改写成 Python。零 LLM，纯正则，只读不改文件。

── 要解决什么问题 ──
简历（user-authored）和"故事库/话术库"（accumulated）不是同一信任级：
故事库里的条目常是从过往面试准备文档里抽出来的，而那些文档本身是
"把你的经历映射到某条 JD 的语言"的产物。于是形成一条洗白通道：

    JD 味措辞 → 被吸收成故事 → 被当作简历同级事实 →
    出现在招呼语/回复/简历里 → 越复用漂得越远

本模块的作用是在**出口**拦一道：任何要发出去的话里的数字，
必须能在用户亲手写的源文件里找到逐字佐证，否则整条剔除。

── 四态（原样保留 career-ops 的语义，勿简化成二元）──
  existing              数字（区间取两端）在源文件里出现；
                        或 story 条目的 Provenance 字段显式写了 user-stated YYYY-MM-DD
  supported_by_resume   数字本身不在源文件，但源文件措辞描述了同一件事
                        （词重叠启发式）→ 疑似相邻，只是这个精度未经核实
  derived_unverified    数字只出现在故事库里，源文件无痕迹，Provenance 也没确认
                        **字段缺失 == 这一态**（安全默认：没标就没特殊信任）
  user_cannot_confirm   Provenance 显式标记。**硬覆盖**：压过数字启发式的任何结论，
                        且永不因为"又被引用一次"而悄悄回到 derived_unverified 或升到 existing

为什么不简化成"已验证/未验证"二元：多年前某份工作的规模数字可能**根本无从考据**。
把"我不知道"和"还没查"混为一谈，会诱导未来某次重扫（或一次诱导性的确认提问）
把猜测悄悄洗成"已核实"。所以第三态必须耐用存在。
"""
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

EXISTING = "existing"
SUPPORTED = "supported_by_resume"
DERIVED = "derived_unverified"
CANNOT = "user_cannot_confirm"

# 出口闸：这四态里只有前两态允许出现在"要发出去的话"里
TRANSMITTABLE = (EXISTING, SUPPORTED)

# ── 数字提取 ──
# 支持：3万+ / 5700 / 40% / 1.5万 / 200万 / 12.5K / 3-5万 / 12,000 / 8000元 / 5 个月 / 5 分钟
# ⚠️ 单位表必须够宽：2026-09-12 踩坑 —— 首版只列了 个|条|次|家|人|天|小时，
# 于是 "5 个月" 只抓到 "5"，单位丢失，导致 "5 分钟" 与 "5 个月" 被判成同一个数。
_UNIT_WORDS = ("个月|分钟|小时|月|年|周|日|天|秒|分|组|份|台|套|款|项|位|轮|步|页|封|倍"
               "|个|条|次|家|人|元|字|页")
_NUM_TOKEN = rf"\d+(?:[.,]\d+)?\s*(?:万|千|k|K|%|{_UNIT_WORDS})?"
# ⚠️ 词边界必须用 ASCII 类而非 \w：Python 3 的 \w 把中文也算进去，
# 于是 "5 个月" 匹配到单位后，后面紧跟的「的」会被当成单词字符 → lookahead 失败
# → 回退成只匹配 "5"，单位被丢弃（2026-09-12 踩坑，见 split_value_unit 说明）。
_NUM_RE = re.compile(
    rf"(?<![A-Za-z0-9_.]){_NUM_TOKEN}(?:\s*[-–~到至]\s*{_NUM_TOKEN})?(?![A-Za-z0-9_])")
_RANGE_SPLIT = re.compile(r"\s*[-–~到至]\s*")

_UNIT_SCALE = {"万": 10_000, "千": 1_000, "k": 1_000, "K": 1_000}


def split_value_unit(token: str) -> Tuple[str, str]:
    """把数字 token 拆成 (归一化数值, 单位)。

    '1.5万' → ('15000','')      '3万+条' → ('30000','+条')
    '99.9%' → ('99.9','%')      '5 个月' → ('5','个月')     '5700条' → ('5700','条')

    ⚠️ 单位必须参与比对。2026-09-12 踩坑：首版只比数值，导致
    `5 分钟` 因为简历里存在 `5 个月` 就被判"有佐证"放行——
    career-ops 的原文是 "the same number, **with a matching unit**"。
    """
    s = str(token or "").strip().replace(",", "").replace(" ", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(万|千|k|K)?\s*(%?)\s*(.*)$", s)
    if not m:
        return "", ""
    num, scale, pct, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    val = float(num) * _UNIT_SCALE.get(scale, 1) if scale else float(num)
    v = str(int(val)) if abs(val - int(val)) < 1e-9 else str(val)
    unit = ("%" if pct else "") + rest.strip()
    return v, unit


def is_claim(value: str, unit: str) -> bool:
    """是否算「事实主张」。

    带单位的数字（5 分钟 / 40% / 5700 条）算主张；
    不带单位的小整数（"从 0 到 1"、3 层分工）是修辞，不算——
    否则出口闸会把正常句子改得支离破碎。
    """
    if not value:
        return False
    if unit:
        return True
    try:
        return abs(float(value)) >= 100
    except ValueError:
        return False


def extract_claims(text: str) -> List[Tuple[str, str]]:
    """抽取(数值, 单位)主张对，保序去重。"""
    if not text:
        return []
    out, seen = [], set()
    for m in _NUM_RE.finditer(text):
        for p in [x for x in _RANGE_SPLIT.split(m.group(0)) if x]:
            v, u = split_value_unit(p)
            if not v:
                continue
            key = (v, u)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _pair_in(allowed: set, value: str, unit: str) -> bool:
    """数值相等 且（单位相同，或任一侧无单位）。"""
    for av, au in allowed:
        if av == value and (au == unit or not au or not unit):
            return True
    return False


def normalize_number(token: str) -> str:
    """把数字 token 归一成可比较的字符串。

    '1.5万' → '15000'；'12,000' → '12000'；'5700条' → '5700'；'40%' → '40%'
    无法归一（如纯汉字）时返回去空白后的原串。
    """
    if token is None:
        return ""
    s = str(token).strip().replace(",", "").replace(" ", "")
    # 去掉中文量词（5700条 与 5700 应当可比；'40%' 不受影响）
    s = re.sub(r"(个|条|次|家|人|天|小时|份|台|套|款|项)$", "", s)
    m = re.match(r"^(\d+(?:\.\d+)?)(万|千|k|K)?(%)?$", s)
    if not m:
        return s
    num, unit, pct = m.group(1), m.group(2), m.group(3)
    val = float(num)
    if unit:
        val *= _UNIT_SCALE[unit]
    if pct:
        return f"{num}%"
    return str(int(val)) if abs(val - int(val)) < 1e-9 else str(val)


def extract_numbers(text: str) -> List[str]:
    """抽取文本里的数字主张（归一后去重，保序）。"""
    if not text:
        return []
    out, seen = [], set()
    for m in _NUM_RE.finditer(text):
        raw = m.group(0)
        # 区间：两端都归一
        parts = [p for p in _RANGE_SPLIT.split(raw) if p]
        for p in parts:
            n = normalize_number(p)
            if not n or not re.search(r"\d", n):
                continue
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


def extract_number_ranges(text: str) -> List[Tuple[str, ...]]:
    """抽取数字主张，区间作为整体返回（'3-5万' → ('30000','50000')）。"""
    if not text:
        return []
    out = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0)
        parts = [p for p in _RANGE_SPLIT.split(raw) if p]
        vals = tuple(normalize_number(p) for p in parts)
        vals = tuple(v for v in vals if v and re.search(r"\d", v))
        if vals:
            out.append(vals)
    return out


# ── 故事库解析 ──
_SECTION_RE = re.compile(r"(?m)^###\s+(.*)$")
_LABEL_RE = re.compile(r"(?m)^\*\*([^*:]+):\*\*\s*(.*)$")


class Story:
    """故事库里的一个条目（`### 标题` 块 + `**Label:** value` 行）。"""

    def __init__(self, title: str, labels: Dict[str, str], raw: str):
        self.title = title
        self.labels = {k.strip().lower(): v.strip() for k, v in labels.items()}
        self.raw = raw

    @property
    def provenance(self) -> str:
        return self.labels.get("provenance", "").strip().lower()

    @property
    def provenance_state(self) -> Optional[str]:
        """Provenance 字段显式表达的状态；无字段/无法识别 → None。"""
        p = self.provenance
        if not p:
            return None
        if p.startswith("user-cannot-confirm"):
            return CANNOT
        if p.startswith("user-stated"):
            return EXISTING
        if p.startswith("source:"):
            return EXISTING
        if p.startswith(DERIVED):
            return DERIVED
        return None


def load_story_bank(path) -> List[Story]:
    """解析 story-bank.md：`### [主题] 标题` 块 + `**Label:** value` 行。"""
    p = Path(path)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="ignore")
    stories = []
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        labels = {lm.group(1): lm.group(2) for lm in _LABEL_RE.finditer(block)}
        stories.append(Story(m.group(1).strip(), labels, block.strip()))
    return stories


# ── 四态判定 ──
def _word_overlap(a: str, b: str, min_len: int = 2) -> float:
    """粗粒度词重叠：中文按 2-gram，英文按词。返回 0-1。"""
    def grams(s: str) -> set:
        s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (s or "").lower())
        toks = set()
        for w in s.split():
            if len(w) >= min_len:
                toks.add(w)
            if re.search(r"[\u4e00-\u9fff]", w):
                for i in range(len(w) - 1):
                    toks.add(w[i:i + 2])
        return toks
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga)


def classify_claim(number: str, sources: Dict[str, str],
                   provenance_state: Optional[str] = None,
                   story_text: str = "",
                   overlap_threshold: float = 0.35) -> Tuple[str, str]:
    """判定单条数字主张的状态。返回 (state, 说明)。

    顺序（career-ops 语义）：
      1. Provenance 显式 user-cannot-confirm → 硬覆盖，直接返回（永不衰减）
      2. Provenance 显式 user-stated / source: → existing
      3. 数字在任一源文件里出现（逐字，含归一形） → existing
      4. 数字不在，但源文件措辞与该故事重叠够高 → supported_by_resume
      5. 否则 → derived_unverified（字段缺失的安全默认）
    """
    if provenance_state == CANNOT:
        return CANNOT, "Provenance 显式 user-cannot-confirm（硬覆盖，永不衰减）"
    if provenance_state == EXISTING:
        return EXISTING, "Provenance 显式确认（user-stated / source:）"

    # 数值 + 单位 双匹配（只比数值会误放行，见 split_value_unit 的踩坑说明）
    claims = extract_claims(str(number)) or [(normalize_number(str(number)), "")]
    allowed = set()
    for text in sources.values():
        if text:
            allowed |= set(extract_claims(text))
    if claims and all(_pair_in(allowed, v, u) for v, u in claims):
        return EXISTING, "数字（含单位）在源文件里逐字/等价出现"

    for fname, text in sources.items():
        if text and _word_overlap(story_text or number, text) >= overlap_threshold:
            return SUPPORTED, f"数字不在 {fname}，但其措辞描述了同一件事（词重叠≥{overlap_threshold}）"

    return DERIVED, "数字仅见于故事库，源文件无痕迹，Provenance 未确认"


def check_story_bank(bank_path, source_paths: Iterable, overlap_threshold: float = 0.35) -> List[dict]:
    """检查整个故事库。返回每条数字主张的判定结果（只读，不写回文件）。"""
    sources = {}
    for sp in source_paths:
        p = Path(sp)
        if p.exists():
            sources[p.name] = p.read_text(encoding="utf-8", errors="ignore")
    results = []
    for story in load_story_bank(bank_path):
        text = story.raw
        for nums in extract_number_ranges(text):
            joined = " - ".join(nums)
            state, why = classify_claim(joined, sources,
                                        provenance_state=story.provenance_state,
                                        story_text=text,
                                        overlap_threshold=overlap_threshold)
            # 区间：两端都要过；一端过一端不过按更保守的算
            if len(nums) > 1:
                subs = [classify_claim(n, sources, story.provenance_state, text,
                                       overlap_threshold)[0] for n in nums]
                if all(s == EXISTING for s in subs):
                    state, why = EXISTING, "区间两端都有逐字佐证"
                elif any(s in (CANNOT,) for s in subs):
                    state, why = CANNOT, "区间任一端为 user-cannot-confirm（硬覆盖）"
                elif not all(s == EXISTING for s in subs):
                    state = SUPPORTED if SUPPORTED in subs else DERIVED
                    why = "区间部分端点无逐字佐证 → 按更保守处理"
            results.append({
                "story": story.title,
                "numbers": joined,
                "state": state,
                "why": why,
                "provenance_field": story.provenance or "(缺失→derived_unverified)",
            })
    return results


# ── 出口硬闸 ──
# ── 技术栈/项目声明核验（job-hunter 扩展，career-ops 没有）──
# career-ops 的 story-provenance 只核**数字**。但 2026-09-15 实测发现更致命的是
# **技术栈声明**：自动起草的 HR 回复里出现了「BOSS直聘助手 Chrome 扩展」和
# 「Chroma+BGE 的 RAG 匹配引擎」——全盘搜不到任何 manifest.json/扩展项目，
# 而 match_engine.py 自述是"纯确定性规则、零第三方依赖、无向量库"。
# 这两条来自 AI 写的面试准备文档（interview-prep/*.md 里的"Chroma 向量库"、
# "BGE vs BM25 对比实验"）——正是"JD 味措辞被吸收成事实"的通道。
# 数字可以不出错，技术栈写错一样在面试现场当场翻车，所以必须一起核。
TECH_CLAIMS = [
    ("Chrome 扩展", r"chrome\s*扩展|扩展程序|浏览器扩展|manifest\.json|extension"),
    ("Chroma 向量库", r"chroma"),
    ("BGE 向量模型", r"\bbge\b"),
    ("向量检索/embedding", r"向量(库|检索|数据)|embedding|sentence[- ]?transform"),
    ("DrissionPage", r"drissionpage"),
    ("RAG 检索增强", r"\brag\b|检索增强"),
    ("Dify", r"dify"),
    ("FastAPI", r"fastapi"),
    ("Flask", r"flask"),
    ("SQLite", r"sqlite"),
    ("LangChain/LangGraph", r"langchain|langgraph"),
    ("95分/球鞋监控", r"95\s*分|95fen|球鞋"),
    ("Playwright/浏览器自动化", r"playwright|浏览器自动化"),
    ("LLM API 集成", r"llm\s*api|大模型\s*api|模型\s*api"),
    ("Prompt 工程", r"prompt"),
]


def check_tech_claims(text: str, corpus: Dict[str, str]) -> dict:
    """核验文本里的技术栈/项目声明是否在「源事实语料」里有对应实物。

    corpus: {来源名: 文本} —— 建议传简历 + 各项目 README（用户亲手写的/项目自述的）。
    返回 {'supported': [(声明, 命中来源)], 'unsupported': [声明], 'checked': n}
    """
    blob = "\n".join(str(v or "") for v in corpus.values()).lower()
    supported, unsupported = [], []
    checked = 0
    for label, pat in TECH_CLAIMS:
        if not re.search(pat, text or "", re.I):
            continue                     # 草稿没提到这项，不用核
        checked += 1
        src = None
        for name, body in corpus.items():
            if body and re.search(pat, str(body), re.I):
                src = name
                break
        if src:
            supported.append((label, src))
        else:
            unsupported.append(label)
    return {"supported": supported, "unsupported": unsupported, "checked": checked}


def gate_outgoing_text(text: str, sources: Dict[str, str],
                       story_bank_path=None) -> dict:
    """出口闸：找出**要发出去的话**里没有逐字佐证的数字主张。

    设计取向与 career-ops 的 negotiation-roi 一致：未经核实的数字**整条剔除**
    （不是打标记、不是"带保留意见地放进去"）。理由：要当众说出口的话里
    站不住的数字，赔的是可信度和机会本身。

    返回 {'blocked': [...], 'ok': [...], 'stripped_text': str}
    """
    allowed = set()
    for t in sources.values():
        allowed |= set(extract_claims(t))
    if story_bank_path:
        for story in load_story_bank(story_bank_path):
            if story.provenance_state in (EXISTING,):
                allowed |= set(extract_claims(story.raw))

    blocked, ok = [], []
    pieces, last = [], 0
    for m in _NUM_RE.finditer(text):
        pairs = [p for p in (split_value_unit(x) for x in _RANGE_SPLIT.split(m.group(0)) if x)
                 if p[0]]
        pairs = [p for p in pairs if is_claim(*p)]
        if not pairs:
            continue                      # 修辞性小整数（"从 0 到 1"）不动它
        joined = " - ".join(f"{v}{u}" for v, u in pairs)
        if all(_pair_in(allowed, v, u) for v, u in pairs):
            ok.append(joined)
            continue
        blocked.append(joined)
        pieces.append(text[last:m.start()])
        pieces.append("[数字待核实]")
        last = m.end()
    pieces.append(text[last:])
    return {"blocked": blocked, "ok": ok, "stripped_text": "".join(pieces)}

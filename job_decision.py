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
import os
import re
from dataclasses import dataclass, field
from typing import Optional

# ── 薪资带（K/月） ──
SALARY_HARD_FLOOR = 5.0      # <5K 默认拒绝

# ── 薪资天花板红线（2026-09-19 用户改稿）──
# 原值 30K（2026-09-05 定：>30K 视为虚高画饼）。
# 实测三天拒掉 112 个岗位，其中大量是深圳正常的 AI 岗
# （AI FDE 21-31K / AI效率工程师 20-36K / 产品总监AI 20-41K），
# 属于「拿防画饼的规则误杀真实高薪岗」——改到 60K，只拦明显离谱的。
# 注意：仍按「上限 > 阈值」判，即 21-31K 这类区间整体不再被误杀。
SALARY_CEILING = 60.0

# ── 过渡线（line="transition"，2026-09-18 用户定）──
# 用途：找「办公室职位」先上岸（拿工资/有工位/能双休），目标是 AI 评测·数据标注 /
# 游戏运营·内容运营 / 内勤文员。与 AI 主线共用同一套制度红线（双休/文化/夜班/校招），
# 但放松两处：薪资下限、以及外包/人服红线（这一档岗位大多挂在人服公司名下）。
TRANSITION_SALARY_FLOOR = 4.0
TRANSITION_PIECE_RATE_WORDS = ["计件", "按件计酬", "按单计酬", "众包", "按量计酬",
                               "多劳多得", "无底薪"]
SALARY_NORMAL_FLOOR = 8.0    # 8-10K 正常可接受
SALARY_PRIORITY = 10.0       # ≥10K 高优先级

# ── 制度红线信号（从 desc/title 结构化提取，命中即死） ──
WORKDAY_REDLINES = [
    "单休", "大小周", "单双休", "996", "007", "上六休一", "做六休一",
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

# ── 届别闸（2026-09-20 定稿）：拦校招/实习，放行合理应届 ──
# 背景：platform_51job / platform_liepin 里原来各有一份内联正则 `[0-9]{2}届`，
# 它会把用户自己的 25届 一起拦掉（用户 2025 届本科毕业，在两年择业期内，不是在校生）。
# 现在收敛到本模块单一实现，两个平台都调这里，禁止再各写一份。
COHORT_REJECT_WORDS = ("校招", "校园招聘", "实习", "实习生")
# 管培生/培训生：任务书 §3.2 没列，但 platform_51job / platform_liepin 原来的内联
# 正则一直在拦它们（2026-09-09 用户定稿）。保留 —— 收敛规则时不得顺手放宽既有过滤，
# 而且「管培生」本身就是校招语境的岗位名，与「拦校招实习」的意图一致。
COHORT_REJECT_WORDS += ("管培生", "培训生")
# 动态年份，禁止硬编码静态年份字符串。
# 自检：25届 / 2025届 / 26届 不被命中（用户是 2025 届，两年择业期内）。
COHORT_FUTURE_RE = re.compile(r"(?:20)?(2[7-9]|[3-9]\d)届")
# ALLOW 名单：这些词表示「这个岗位明确接受我们这一届」。
# 只用来解除「届别过晚」这一条，不解除校招/实习这类身份不匹配。
COHORT_ALLOW_WORDS = ("应届生", "经验不限", "0-1年", "1年以内", "1年经验",
                      "学生可投", "往届毕业生可投")

# ── 兼职/非全日制过滤（2026-09-20 定稿）──
PART_TIME_WORDS = ("兼职", "临时工", "小时工", "日结", "众包",
                   "短期工", "纯提成", "无底薪", "无保底")
# 否定词必须**紧贴**命中词左侧才算「放行」。
# ⚠️ 任务书原文给的「取命中词前 4 个字符窗口，窗口内含否定词就放行」会误判：
#    「非日结兼职」会被前 4 字符窗口里的「非」放行，但它其实是兼职岗。
# 所以这里要求否定词紧邻（中间除空格外不能有别的字）：
#    「非兼职」「拒绝兼职」「不接受兼职」「全职（非日结）」→ 放行
#    「非日结兼职」「短期兼职」「招聘兼职运营」→ 拦截
PART_TIME_NEG_WORDS = ("拒绝", "非", "不招", "严禁", "不接受")

# ── 作息制度判定结果（§3.1：UNKNOWN 必须与 PASS 显式区分）──
SCHEDULE_PASS = "PASS"          # 作息信息真的被检查过（正文有，或标题里有明确作息词）
SCHEDULE_UNKNOWN = "UNKNOWN"    # 拿不到正文、标题也没给作息信号 → 结论只能是"不知道"

# ── 高价值外包（O1/O2 可入，O3/O4 拒）：职业跳板 —— 不在此处判，留给 deep_filter ──

# ── 2026-09-05 用户定稿：底薪口径 + 文化红线 ──
# 用户：底薪(无责底薪) ≥8K 才投，绩效/提成不算；狼性文化/多劳多得公司排除。
CULTURE_REDLINES = ["狼性", "多劳多得", "结果导向", "能者多劳",
                    "奋斗者", "拼搏精神", "996是福报", "业绩为王"]
# 注意：不含"扁平化管理"（中性甚至正面描述，2026-09-05 极近AI音乐制作误杀案）
# 底薪声明（正则）：底薪5K / 无责底薪4500元 / 底薪5000+提成
# 数字后带 K/k/千 = K 单位；不带 = 元（5000元=5K）
BASE_SALARY_RE = r"(?:无责)?底薪[约]?(\d+(?:\.\d+)?)\s*([kK千])?"
BASE_SALARY_RE_CN = r"(?:无责)?底薪[约]?([一二三四五六七八九十百]+)"


def parse_base_salary(text: str):
    """从 JD 文本解析明确声明的无责底薪（K/月）。没有声明返回 None。"""
    if not text:
        return None
    import re
    m = re.search(BASE_SALARY_RE, text)
    if m:
        try:
            val = float(m.group(1))
            unit = m.group(2) or ""
            if unit in ("k", "K", "千"):
                return val
            return val / 1000.0  # 无单位 = 元（5000 → 5K）
        except (ValueError, IndexError):
            return None
    # 中文"底薪八千"类
    m2 = re.search(BASE_SALARY_RE_CN, text)
    if m2:
        cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9, "十": 10}
        s = m2.group(1)
        if len(s) == 1 and s in cn:
            return float(cn[s])
        if "十" in s:
            return float(cn.get(s[0], 1) * 10)
    return None


@dataclass
class Decision:
    action: str                  # ALLOW / REJECT
    priority: str = ""           # HIGH / NORMAL / LOW / ""
    salary_band: str = ""        # <5K / 5-8K / 8-10K / >=10K / unknown
    reason: str = ""
    special_approval: bool = False
    # 作息制度到底有没有被**真的检查过**（§3.1）。
    # 51job 的搜索卡片只有 title/salary，jd_text 是空的 —— 那时作息结论只能是
    # UNKNOWN，绝不能被当成「没命中单休关键词 = 默认双休 = PASS」。
    schedule_verdict: str = SCHEDULE_UNKNOWN

    def __str__(self):
        return f"[{self.action}] {self.priority} | {self.reason}"


def _decode_salary_text(salary: str) -> str:
    """薪资文本预处理：去空格逗号小写 + Boss PUA 图标数字解码(0xe030-0xe039=0-9)。"""
    if not salary:
        return ""
    s = salary.replace(" ", "").replace(",", "").lower()
    if any(0xE030 <= ord(ch) <= 0xE039 for ch in s):
        s = "".join(chr(ord(ch) - 0xE030 + ord("0"))
                    if 0xE030 <= ord(ch) <= 0xE039 else ch for ch in s)
    return s


def _strip_pay_suffix(s: str) -> str:
    """去掉「·13薪 / -14薪 / 13薪」等薪数后缀。

    2026-09-12 修复: 51job/Boss 常见 "2.5-5万·13薪"。原 _parse_salary_value 用
    float(s.replace("万","")) 解析，得到 "2.5·13薪" → ValueError → 返回 0.0，
    于是 high=0 → `high > SALARY_CEILING` 永不成立 → 天花板红线对该类岗位完全失效（漏投）。
    """
    return re.sub(r"[·\-]?\s*\d+\s*薪", "", s)


def _parse_salary_value(s: str) -> float:
    """单段薪资文本 → K/月 数值。s 已解码且无区间。"""
    try:
        s = _strip_pay_suffix(s)
        # 只取前导数字，忽略残余后缀（如残留的 "/月"、单位外的杂字符）
        m = re.match(r"^(\d+(?:\.\d+)?)", s)
        if not m:
            return 0.0
        num = float(m.group(1))
        if "万" in s:
            # 2026-09-10 修复: 年薪格式(15-22万/年)曾被当"15万月薪"=150K误拦30K线。
            # 年薪 ÷12 折月薪: 15万/年 → 12.5K/月
            if "年" in s:
                return num * 10 / 12
            return num * 10
        if "k" in s:
            return num
        if "元/天" in s or "元/日" in s:
            return num * 22 / 1000
        if "元/月" in s:
            return num / 1000
        if "元" in s:
            return num / 1000
        # 裸数字（Boss 区间 "4-7K" 前半段是 "4"）= 默认 K
        return num
    except (ValueError, IndexError):
        return 0.0
    return 0.0


def parse_salary_low(salary: str) -> float:
    """解析薪资下限(K/月)。0 = 未知(不因未知拒绝)。"""
    s = _decode_salary_text(salary)
    if not s:
        return 0.0
    try:
        if "-" in s:
            # 区间："4-7K" / "411-511元/天" — 单位在尾部，两段都带同一单位
            parts = s.split("-")
            unit = ""
            for u in ("万/年", "万/月", "元/天", "元/日", "元/月", "元", "k", "万"):
                if u in parts[1]:
                    unit = u
                    break
            return _parse_salary_value(parts[0] + unit)
        return _parse_salary_value(s)
    except (ValueError, IndexError):
        return 0.0


def parse_salary_high(salary: str) -> float:
    """解析薪资上限(K/月)。0 = 未知。用于薪资天花板红线判断(>SALARY_CEILING 不投)。"""
    s = _decode_salary_text(salary)
    if not s:
        return 0.0
    try:
        if "-" in s:
            return _parse_salary_value(s.split("-")[1])
        # 单值(如"50K"或"面议")：上限=下限=该值（50K单值也会被>30K拦）
        return _parse_salary_value(s)
    except (ValueError, IndexError):
        return 0.0


def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


def cohort_block_reason(title: str) -> Optional[str]:
    """届别闸。返回拦截原因；返回 None 表示「届别规则不阻断」。

    注意：None 只表示届别这一条不拦，后续薪资/制度/主体闸门照常生效。
    判定顺序：校招/实习这类**身份**信号最硬，先拦；再看届别年份。
    """
    t = title or ""
    if not t:
        return None
    hit = next((w for w in COHORT_REJECT_WORDS if w in t), None)
    if hit:
        return f"届别闸:{hit}"
    # 岗位明确接受我们这一届（应届生/往届可投/经验不限…）→ 解除「届别过晚」
    if _has_any(t, COHORT_ALLOW_WORDS):
        return None
    m = COHORT_FUTURE_RE.search(t)
    if m:
        return f"届别闸:届别过晚({m.group(0)})"
    return None


def is_part_time_job(title: str, jd_snippet: str = "") -> bool:
    """兼职/非全日制岗判定。True = 拦截。

    否定词必须紧贴命中词左侧才算放行（见 PART_TIME_NEG_WORDS 处注释）：
      「非兼职」「拒绝兼职」「不接受兼职」「全职（非日结）」→ False（放行）
      「非日结兼职」「短期兼职」「客服小时工」「招聘兼职运营」→ True（拦截）

    ⚠️ jd_snippet 为空时**只**看 title —— 不得拿空正文跑一遍假装"扫过正文了"。
    """
    text = f"{title or ''} {jd_snippet or ''}"
    if not text.strip():
        return False
    for w in PART_TIME_WORDS:
        start = text.find(w)
        while start != -1:
            before = text[max(0, start - 4):start].replace(" ", "").replace("　", "")
            if not any(before.endswith(n) for n in PART_TIME_NEG_WORDS):
                return True
            start = text.find(w, start + 1)
    return False


def schedule_verdict(title: str, desc: str) -> str:
    """作息制度判定结果：PASS / UNKNOWN（§3.1）。

    正文拿得到 → PASS（有没有命中红线由其他闸门负责）。
    正文拿不到（51job 搜索卡片只有 title/salary，jd_text 为空）→ 只有标题里出现
    明确作息词时才算「看过」，否则一律 UNKNOWN。
    **UNKNOWN 不等于「没有命中单休关键词」，更不等于「默认双休」。**
    """
    if (desc or "").strip():
        return SCHEDULE_PASS
    if _has_any(f"{title or ''}", WORKDAY_REDLINES + DOUBLE_REST_SIGNALS):
        return SCHEDULE_PASS
    return SCHEDULE_UNKNOWN


def evaluate_job(company: str, title: str, desc: str, salary: str,
                 city: str = "", cfg=None,
                 parsed_signals: Optional[dict] = None,
                 line: str = "ai") -> Decision:
    """确定性岗位裁决（对外入口）。

    与 _evaluate_job_core 的区别只有一个：**统一补上作息制度的判定结果**。
    放在外层补，是为了不破坏 core 里那一串 return（每个分支都手写一遍
    容易漏，也容易在改动时忘掉某一支）。
    """
    d = _evaluate_job_core(company, title, desc, salary, city, cfg,
                           parsed_signals, line)
    d.schedule_verdict = schedule_verdict(title, desc)
    return d


def _evaluate_job_core(company: str, title: str, desc: str, salary: str,
                       city: str = "", cfg=None,
                       parsed_signals: Optional[dict] = None,
                       line: str = "ai") -> Decision:
    """确定性岗位裁决。

    parsed_signals: 模型/解析层提供的结构化信号（可选）：
      {"workday": "single_rest"|"double_rest"|"unknown",
       "shift": bool, "outsourcing": bool, "intern": bool}
    提供后以信号为准；未提供则用关键词提取兜底。
    """
    # 线路切换：JH_LINE=transition 时走过渡线规则（不改调用方代码即可切换）
    if line == "ai":
        line = os.environ.get("JH_LINE", "ai")
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

    # ── 0.5 届别闸 + 兼职闸（2026-09-20 新增，收敛原先散在平台的重复实现）──
    # 顺序放在制度红线之前：身份不匹配（校招/实习/兼职）比作息更根本。
    _cohort = cohort_block_reason(title)
    if _cohort:
        return Decision("REJECT", reason=_cohort)
    if is_part_time_job(title, desc):
        return Decision("REJECT", reason="兼职/非全日制岗→过滤")

    # ── 1. 关键词兜底：制度红线 ──
    # 用户在 2026-09-16 定稿：**至少双休**。单休/大小周/996/007/夜班/轮班 一律命中即死，
    # 不再有薪资例外（原「大小周 ≥12K 可谈」的特批通道已废除，见 tests 中对应用例）。
    _hit = _has_any(combined, WORKDAY_REDLINES)
    if _hit:
        hit = next(w for w in WORKDAY_REDLINES if w in combined)
        return Decision("REJECT", reason=f"制度红线:{hit}")
    if sig.get("workday") == "unknown" and _has_any(title, ["轮班", "夜班", "倒班"]):
        return Decision("REJECT", reason="制度红线:标题轮班/夜班")

    # ── 1.5 文化红线（2026-09-05 用户定稿）：狼性/多劳多得公司直接排除 ──
    _culture_hit = _has_any(combined, CULTURE_REDLINES)
    if _culture_hit:
        hit = next(w for w in CULTURE_REDLINES if w in combined)
        return Decision("REJECT", reason=f"文化红线:{hit}")

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
    high = parse_salary_high(salary)
    # 2026-09-19 改：天花板 30K → 60K（SALARY_CEILING）。上限或单值 >60K 直接拒。
    if high > SALARY_CEILING or (high <= 0 and low > SALARY_CEILING):
        return Decision("REJECT", priority="", salary_band=f">{SALARY_CEILING:g}K",
                        reason=f"薪资超{SALARY_CEILING:g}K红线({salary[:16]}→high={high:g}K)→不投,不真实")
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
        # 过渡线（2026-09-18）：办公室岗现实区间就是 3.5-6K，≥4K 放行上岸优先。
        if line == "transition":
            _num = None
            _m = re.search(r"(\d+(?:\.\d+)?)\s*(?:千|[kK])", salary or "")
            if _m:
                _num = float(_m.group(1))
            if _num is not None and _num >= TRANSITION_SALARY_FLOOR:
                return Decision("ALLOW", priority="LOW", salary_band=band,
                                reason=f"{band}过渡线:≥{TRANSITION_SALARY_FLOOR:g}K→上岸优先")
            return Decision("REJECT", priority="", salary_band=band,
                            reason=f"{band}过渡线:低于{TRANSITION_SALARY_FLOOR:g}K仍拒")
        # 特批检查：即使<5K，正式工/编制/极高稳定也留一条缝（用户场景少但存在）
        if _has_any(combined, SPECIAL_APPROVAL_SIGNALS):
            return Decision("ALLOW", priority="LOW", salary_band=band,
                            special_approval=True,
                            reason=f"{band}特批:正式工/编制/高稳定信号")
        return Decision("REJECT", priority="", salary_band=band, reason=f"{band}→默认拒绝")
    if band == "5-8K" and line == "transition":
        # 过渡线：5-8K 直接可投（不再要求特批信号）；但计件/无底薪一律拒
        # （调研结论：计件是这一档最主要的坑，单价会被压、月薪波动大）。
        if _has_any(combined, TRANSITION_PIECE_RATE_WORDS):
            return Decision("REJECT", priority="", salary_band=band,
                            reason=f"{band}过渡线:计件/无底薪→拒（月薪不稳）")
        return Decision("ALLOW", priority="LOW", salary_band=band,
                        reason=f"{band}过渡线→可投(LOW)")
    if band == "5-8K":
        # 2026-09-05 用户定稿：底薪(无责底薪) ≥8K 才投，绩效/提成不算。
        # 区间下限 5-8K 的岗：若 JD 明确声明底薪≥8K（如"底薪8K+高提成"）→ 放行；
        # 否则默认拒（标注区间都不到8K，底薪大概率更低，聚客7K底薪5K是反面案例）。
        _base = parse_base_salary(combined)
        if _base is not None and _base >= 8.0:
            return Decision("ALLOW", priority="LOW", salary_band=band,
                            reason=f"{band}但底薪{_base:g}K达标→可投(LOW)")
        if _has_any(combined, SPECIAL_APPROVAL_SIGNALS):
            return Decision("ALLOW", priority="LOW", salary_band=band,
                            special_approval=True,
                            reason=f"{band}特批:正式工/编制/高稳定信号")
        return Decision("REJECT", priority="", salary_band=band,
                        reason=f"{band}→底薪口径不达标,拒绝")
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
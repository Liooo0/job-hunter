#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""制度问询状态机（2026-09-20 §3.1 后置兜底）。

【为什么需要它】
51job 的搜索列表卡片**只有 title/salary，`jd_text` 为空**（数据库 applications_v2 实测）。
所以「双休 / 排班 / 加班」这类必须读正文才能确认的信息，在投递那一刻根本拿不到 ——
此时唯一诚实的结论是 `UNKNOWN`（见 job_decision.SCHEDULE_UNKNOWN），
既不能等价成「没命中单休关键词」，更不能默认成「双休」。

既然搜索期确认不了，就把它挪到**投递之后**：等 HR 真的开口聊了（索取简历、
问到岗时间、约面试），再顺便问一句。这比在标题上瞎猜靠谱，也比事后才发现踩坑便宜。

【触发条件（OR，任一命中）】
    is_substantive = has_substantive_text_reply(msg)   # HR 人工回复了实质内容
                     or asks_for_full_resume(msg)      # 索取完整/附件简历
                     or asks_for_onboarding_time(msg)  # 问最快到岗 / 是否在职
                     or invites_to_interview(msg)      # 发起面试 / 电话沟通

【严禁触发的场景】首句打招呼、系统自动招呼、岗位推荐推送、已读未回、婉拒回复。
这些都在 `is_substantive()` 里前置挡掉 —— 顺序很重要：Boss 的 HR 端自动招呼常常
长得就像「您好，方便发份简历吗？」，如果先跑 `asks_for_full_resume` 就会误判成
「HR 在要简历」，于是给一句机器人群发的话回一段制度问询。

【只问一次】状态落在 `data/asked_state.json`，键 = 公司+HR名。
为什么不用「在 reply_pending.json 的会话条目上加 schedule_asked_at」：
reply_pending 的条目会在审核结束后转成 sent/rejected，同一 HR 发**下一条**新消息时
会生成一条全新条目（reply_lock.acquire 的去重键含消息正文，见该处注释），
新条目上没有 schedule_asked_at → 又会被问一遍，正好违背「同一 HR 只问一次」。
按「人」而不是按「消息」记状态才是对的：问的是这个 HR，不是这条消息。
（`data/` 整体在 .gitignore 里，运行数据不进 Git。）
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).parent
DATA_DIR = SKILL_DIR / "data"
STATE_FILE = DATA_DIR / "asked_state.json"

# ── 问询话术（用户定稿，固定文本）──
# 不加「您好/感谢」类客套：这句是接在正常对话后面的追加提问，不是开场白。
INQUIRY_TEXT = "顺便确认下：部门是固定双休还是排班轮休？上下班节奏大概怎样？"

# ── 系统 / 推送 / 非人工消息：出现即一律不触发 ──
SYSTEM_MARKERS = (
    "您正在与Boss", "您正在与boss", "系统消息", "系统提示", "自动回复", "机器人",
    "该职位已下线", "职位已关闭", "职位已下线", "撤回了一条消息", "您的附件简历",
    "对方已读", "对方未读", "已读未回",
)
RECOMMEND_MARKERS = (
    "为你推荐", "为您推荐", "猜你喜欢", "岗位推荐", "职位推荐", "推荐岗位",
    "推荐职位", "可能感兴趣", "根据您的简历", "更多相似", "相似职位",
)
# 纯打招呼（首句/系统自动招呼）：除了"嗨"什么都没有
GREETING_ONLY_RE = re.compile(
    r"^[^0-9A-Za-z]{0,6}(?:您好|你好|hi|hello|哈喽|在吗|在么|在不在|有人吗)"
    r"[！!。.,，~～\s、]*$", re.I)
# 系统自动招呼：以问候语开头、且整句只由「模板动作」拼成（自我介绍 / 推销岗位 / 要简历）。
# Boss 的 HR 端自动招呼就长这样：「您好，我是XX公司的HR，方便发份简历吗？」
# 它同时命中 asks_for_full_resume，如果不单独挡掉，就会给一句机器群发的话回制度问询。
# 判据取「短 + 以问候开头 + 只有模板动作」：真人聊起来后写的话通常更长、也不会再以问候开头。
GREETING_OPENERS = ("您好", "你好", "哈喽", "hi", "hello", "在吗", "在么")
AUTO_TEMPLATE_MARKERS = (
    "我是", "招聘", "岗位", "职位", "方便发份简历", "方便发一份简历", "发份简历",
    "看到您的简历", "对您的工作经历", "对您的经历", "对您的履历", "聊聊", "沟通一下",
)
AUTO_GREETING_MAX_LEN = 40
# 婉拒 / 收尾类：HR 明确说不合适时不该反过来问人作息
REJECT_MARKERS = (
    "不合适", "不匹配", "不太匹配", "暂不", "暂时不", "已招满", "招够", "招满",
    "不太合适", "抱歉", "抱歉了", "很遗憾", "祝您", "感谢关注", "未通过", "没通过",
    "通过不了", "不符合", "已找到", "已招到", "有机会再",
)
# 模板回执（非模板 ≠ 实质）
TEMPLATE_MARKERS = (
    "感谢您对", "期待您的加入", "祝您生活愉快", "祝您求职顺利",
    "您的简历我们已收到", "已收到您的简历", "简历已收到", "我们会尽快", "请耐心等待",
)
# 纯应答（没有信息量）
ACK_ONLY = {"好的", "好的谢谢", "好的，谢谢", "收到", "收到谢谢", "收到，谢谢", "谢谢",
            "谢谢您", "嗯", "嗯嗯", "哦", "好的呢", "行", "好", "ok", "okay", "在的", "在"}

# ── 实质内容判定的提示词（HR 在聊岗位/经历，而不是在发表情包）──
SUBSTANTIVE_HINT_WORDS = (
    "经验", "技能", "项目", "作品", "薪资", "期望", "岗位", "职责", "团队", "业务",
    "产品", "技术", "几年", "做过", "了解过", "介绍", "聊聊", "沟通", "方便", "简历",
    "面试", "电话", "在职", "离职", "到岗", "入职", "学历", "专业", "背景", "招",
)

# ── 索取简历 ──
RESUME_WORDS = ("简历", "附件简历", "作品集", "portfolio", "详细经历")
RESUME_ASK_WORDS = ("发", "份", "下", "个", "看", "要", "给", "提供", "上传",
                    "有没有", "有吗", "方便", "麻烦", "需要")
# ── 问到岗 / 在职状态 ──
ONBOARDING_WORDS = (
    "到岗", "到职", "入职", "最快什么时候", "多久能到", "多久到", "什么时候能来",
    "什么时候可以来", "目前在职", "现在在职", "是否在职", "在职吗", "离职", "在看机会",
    "目前状态", "什么时候方便", "什么时候有时间", "哪天有空", "什么时间有空",
    "最快多久", "什么时候到",
)
# ── 发起面试 / 电话沟通 ──
INTERVIEW_WORDS = (
    "面试", "面谈", "约面", "复试", "初试", "二面", "电话沟通", "电话聊",
    "电话联系", "电话吗", "通话", "视频面", "线上面", "来公司聊",
)
INTERVIEW_ACTION_WORDS = (
    "安排", "约", "参加", "方便", "时间", "什么时候", "哪天", "聊", "沟通",
    "电话", "打", "视频", "线上", "来", "定",
)
_ASK_MARKS = ("?", "？", "吗", "呢", "方便", "请问", "问下", "想问", "确认下", "了解下")


def _clean(msg: str) -> str:
    """去所有空白（Boss 消息里夹换行/空格很常见，逐字匹配必须先归一）。"""
    return re.sub(r"\s+", "", msg or "")


def _is_ask(t: str) -> bool:
    """是否带着"在问"的语气。用于把「陈述」和「提问」分开。"""
    return any(m in t for m in _ASK_MARKS)


def looks_like_auto_greeting(msg: str) -> bool:
    """像不像一句机器群发的开场招呼（自我介绍/推销岗位/要简历的模板话术）。

    见 AUTO_TEMPLATE_MARKERS 上方注释：这类消息会同时命中 asks_for_full_resume，
    必须在 OR 之前挡掉，否则会给群发的模板话回一段制度问询。
    """
    t = _clean(msg)
    if not t or len(t) > AUTO_GREETING_MAX_LEN:
        return False
    if not any(t.lower().startswith(o) for o in GREETING_OPENERS):
        return False
    return any(m in t for m in AUTO_TEMPLATE_MARKERS)


def has_substantive_text_reply(msg: str) -> bool:
    """HR 产生了非系统模板的实质性文字回复（在聊岗位细节/候选人的经历）。"""
    t = _clean(msg)
    if len(t) < 8:
        return False
    if t in ACK_ONLY:
        return False
    if any(m in t for m in TEMPLATE_MARKERS):
        return False
    return ("?" in t or "？" in t) or any(w in t for w in SUBSTANTIVE_HINT_WORDS)


def asks_for_full_resume(msg: str) -> bool:
    """索取完整/附件简历。"""
    t = _clean(msg)
    if not any(w in t for w in RESUME_WORDS):
        return False
    return any(w in t for w in RESUME_ASK_WORDS)


def asks_for_onboarding_time(msg: str) -> bool:
    """询问最快到岗时间 / 是否在职。"""
    t = _clean(msg)
    if not any(w in t for w in ONBOARDING_WORDS):
        return False
    return _is_ask(t)


def invites_to_interview(msg: str) -> bool:
    """发起面试 / 电话沟通邀请。"""
    t = _clean(msg)
    if not any(w in t for w in INTERVIEW_WORDS):
        return False
    return _is_ask(t) or any(w in t for w in INTERVIEW_ACTION_WORDS)


def is_substantive(msg: str) -> bool:
    """后置状态机总入口：这条 HR 消息算不算「真的聊起来了」。

    否定判断必须放在 OR 之前 —— 见模块 docstring 里自动招呼那条。
    """
    t = _clean(msg)
    if len(t) < 2:
        return False          # 已读未回 / 空消息
    if any(m in t for m in SYSTEM_MARKERS):
        return False          # 系统消息、系统自动招呼
    if any(m in t for m in RECOMMEND_MARKERS):
        return False          # 岗位推荐推送
    if GREETING_ONLY_RE.match(t):
        return False          # 首句打招呼（模板自动招呼大多是这一种）
    if looks_like_auto_greeting(t):
        return False          # 系统自动招呼：像「您好，我是XX的HR，方便发份简历吗？」
    if any(m in t for m in REJECT_MARKERS):
        return False          # 婉拒：不该反过来问人家作息
    return (has_substantive_text_reply(t)
            or asks_for_full_resume(t)
            or asks_for_onboarding_time(t)
            or invites_to_interview(t))


# ── "已问过" 持久化 ──

def _key(company: str, hr_name: str) -> str:
    return f"{(company or '').strip()}|{(hr_name or '').strip()}"


def _load() -> dict:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("asked"), dict):
                return data
    except Exception:
        pass
    return {"asked": {}}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)   # 原子替换，别让半截文件把状态读废


def has_asked(company: str, hr_name: str) -> bool:
    """这个 HR 是否已经被问过制度。"""
    return _key(company, hr_name) in _load().get("asked", {})


def mark_asked(company: str, hr_name: str, session_id: str = "",
               note: str = "") -> dict:
    """登记「已问过」。返回写入的条目。"""
    data = _load()
    key = _key(company, hr_name)
    entry = data["asked"].get(key) or {}
    entry.update({
        "company": company, "hr_name": hr_name,
        "asked_at": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id or entry.get("session_id", ""),
        "text": INQUIRY_TEXT,
        "ask_count": int(entry.get("ask_count", 0)) + 1,
    })
    if note:
        entry["note"] = note
    data["asked"][key] = entry
    _save(data)
    return entry


def ask_once(company: str, hr_name: str, session_id: str = "") -> str:
    """**唯一对外入口**：该问就落盘并返回问询文本；已问过返回空串。

    调用方拿到空串就什么都别加 —— 这就是「同一 HR 只问一次」的实现。
    """
    if has_asked(company, hr_name):
        return ""
    mark_asked(company, hr_name, session_id=session_id)
    return INQUIRY_TEXT


def state() -> dict:
    return _load().get("asked", {})


def reset(company: str = None) -> int:
    """清掉「已问过」记录（company=None 清全部）。运维/调试用。返回清掉的条数。"""
    data = _load()
    asked = data.get("asked", {})
    if company is None:
        n = len(asked)
        data["asked"] = {}
    else:
        keys = [k for k in asked if k.startswith(f"{company.strip()}|")]
        n = len(keys)
        for k in keys:
            asked.pop(k, None)
    _save(data)
    return n


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        st = state()
        if not st:
            print("📭 尚未向任何 HR 问过作息制度")
        else:
            print(f"🩺 已问过 {len(st)} 个 HR 的作息制度：")
            for k, v in st.items():
                print(f"   {k} | {v.get('asked_at','?')} | 问过{v.get('ask_count',1)}次"
                      f" | {v.get('session_id','')}")
    elif cmd == "clear":
        print(f"🧹 已清除 {reset(sys.argv[2] if len(sys.argv) > 2 else None)} 条记录")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

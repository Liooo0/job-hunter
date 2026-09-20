#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自方消息留痕（2026-09-20 §3.5）。

【为什么需要这个模块】
判定「会话最后一条是不是我自己发的」这件事，原来靠比对**文案开头几个字**：
    sync_chat_status._self_marks = ["您好！我是", "我是"]
    hr_auto_reply._is_hr_real_message: f"您好！我是{MY_NAME}" / startswith("您好！我是")
这是**文案耦合**：招呼语一改（§3.5 正好就要改），我们自己发出去的话立刻认不出来了
→ 被当成 HR 新消息扫进待回复队列 → 误触发 REPLY_REVIEW_LOCK → 投递被挂起。
换文案就再坏一次，所以「再加一个固定前缀」不是解法。

【解法：记「我们发过什么」，而不是猜「我们的话长什么样」】
任何**真实发送动作**都在这里落一条留痕：
    · boss_apply  发出去的 Boss 招呼语
    · hr_auto_reply.send_one 发出去的 HR 回复
判定时拿留痕比对 —— 与文案内容、语气、开头词完全无关，改多少次文案都不会漏判。

留痕同时兼容既有来源（都已存在，不是新造的）：
    · reply_lock 队列里 status='sent' 的草稿（人工确认后发出的回复）
    · sent_replies/ archived_replies/ 里 my_reply 字段（历史回复留痕）
这样即使某条消息是这次改动之前发的，只要它走过上面任一条通路，照样认得出来。

⚠️ data/ 整体在 .gitignore 里，留痕是运行数据，不进 Git。
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).parent
DATA_DIR = SKILL_DIR / "data"
SENT_FILE = DATA_DIR / "self_sent.json"

# 留痕只用来判方向，不需要无限增长；保留最近 N 条（远超一轮投递量）。
MAX_KEEP = 800
# 前缀匹配的最小长度：语料里存在「您好」这种 2 字消息，拿它去 startswith
# 会把所有以「您好」开头的消息全判成我方（sync_chat_status._corpus_hit 踩过同样的坑）。
MIN_PREFIX = 8


def _norm(text: str) -> str:
    """归一：去所有空白。DOM 取文本时常夹换行/空格，逐字比对前必须先归一。"""
    return re.sub(r"\s+", "", text or "")


def record(text: str, channel: str = "", company: str = "", name_box: str = "") -> bool:
    """登记一条**真的发出去**的消息。空文本不登记，返回是否新增。

    只应在发送动作被验证成功后调用 —— 没发出去的话不能留痕，否则会把
    「我打算发但失败了」记成「我发过了」，反而把 HR 的话误判成我方消息。
    """
    t = _norm(text)
    if len(t) < 2:
        return False
    data = _load()
    items = data.get("sent", [])
    if any(_norm(i.get("text", "")) == t for i in items):
        return False
    items.append({
        "text": text,
        "norm": t[:200],
        "channel": channel,
        "company": company,
        "name_box": name_box,
        "sent_at": datetime.now().isoformat(timespec="seconds"),
    })
    data["sent"] = items[-MAX_KEEP:]
    _save(data)
    return True


def _load() -> dict:
    try:
        if SENT_FILE.exists():
            d = json.loads(SENT_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("sent"), list):
                return d
    except Exception:
        pass
    return {"sent": []}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SENT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, SENT_FILE)


def sent_texts() -> set:
    """全部留痕正文（归一后）。"""
    return {i.get("norm") or _norm(i.get("text", "")) for i in _load().get("sent", [])}


def _other_sources() -> set:
    """既有留痕来源：reply_lock 已发送草稿 + sent_replies/archived_replies 的 my_reply。"""
    out = set()
    try:
        import reply_lock as _RL
        for s in _RL.pending():
            if s.get("status") == "sent" and s.get("draft"):
                out.add(_norm(s["draft"]))
    except Exception:
        pass
    for d in ("sent_replies", "archived_replies"):
        p = SKILL_DIR / d
        if not p.exists():
            continue
        for f in p.glob("*.json"):
            try:
                v = json.loads(f.read_text(encoding="utf-8")).get("my_reply") or ""
            except Exception:
                continue
            if v.strip():
                out.add(_norm(v))
    return out


def self_sent_pool() -> set:
    """全部我方消息正文（归一后）：本次留痕 + reply_lock 已发送 + 历史回复留痕。

    读盘代价不高但会被反复调用（扫描时每个会话问一次），调用方可自行缓存 ——
    提供纯函数 `matches()` 就是为了让调用方缓存 pool 之后不必再碰 IO。
    """
    return sent_texts() | _other_sources()


def matches(msg: str, pool: set) -> bool:
    """纯匹配：这条消息在不在这堆我方消息里。见 is_self_sent 的规则说明。"""
    m = _norm(msg)
    if len(m) < 2:
        return False
    for t in pool:
        if not t:
            continue
        if m == t:
            return True
        if len(t) >= MIN_PREFIX and len(m) >= MIN_PREFIX:
            if m.startswith(t[:MIN_PREFIX]) or t.startswith(m[:MIN_PREFIX]):
                return True
    return False


def is_self_sent(msg: str, extra: set = None) -> bool:
    """这条消息是不是我们自己发出去的（只看留痕，不看文案长什么样）。

    匹配规则：
      · 归一后完全相等 → 是
      · 一方是另一方的**前缀**且两边都 ≥ MIN_PREFIX → 是
        （DOM 会把长消息截断，比如聊天列表只取前 30 字）
    """
    pool = self_sent_pool() | {_norm(x) for x in (extra or set())}
    return matches(msg, pool)


def main() -> None:
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        items = _load().get("sent", [])
        print(f"📝 自方消息留痕 {len(items)} 条"
              f"（另有 reply_lock / sent_replies 来源 {len(_other_sources())} 条）")
        for i in items[-10:]:
            print(f"   {i.get('sent_at','?')} [{i.get('channel','')}] "
                  f"{(i.get('text') or '')[:50]}")
    elif cmd == "check":
        q = sys.argv[2] if len(sys.argv) > 2 else ""
        print(f"{'我方消息' if is_self_sent(q) else '不是我方消息'} ← {q[:60]}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

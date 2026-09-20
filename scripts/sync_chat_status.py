#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聊天状态自动回填（2026-09-15 新增）—— 把 Boss 聊天的真实状态写回漏斗

── 为什么 ──
ab_experiment.db 里 44,000+ 条记录，read / replied / interview / offer **全是 0**：
7 段漏斗（投递→已读→回复→面试→技术面→Offer）系统建好了，但后半段一个数据都没有
（原设计靠人工补录，实际没人补）。于是「哪类岗位值得投、哪版简历有效」这些问题
永远无法回答 —— funnel_report.py 自己也写着「要么做投递后验证，要么承认这个数字
不可用于复盘」。

── 做什么（只读，绝不发消息、绝不点击）──
连上已经登录的 Boss 聊天页（调试端口），读会话列表里每条会话的真实状态：
  · `.message-status.status-read`（列表里那个「已读」标记）→ 该岗位 read=1
  · 最后一条消息是 HR 真实消息（复用 hr_auto_reply 的判定）→ replied=1
  · 消息里出现面试邀约信号 → 只作为「候选」推送提醒，**不自动写 interview**
    （面试是不可逆的高价值状态，宁可人工确认一次）

写库是单向的：只把 0 翻成 1，绝不回退；每家公司只认最近 30 天内的投递记录。

── 用法 ──
    python3 scripts/sync_chat_status.py                # 预演（默认，不写库）
    python3 scripts/sync_chat_status.py --apply        # 写库 + 有新回复时推微信
    python3 scripts/sync_chat_status.py --port 9223 --days 30
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from store import normalize_company  # noqa: E402

# 本人姓名从本机档案读（公开仓库不留真名）。读不到就退化为"未配置"，判定逻辑仍成立。
try:
    from hr_auto_reply import MY_NAME as _MY_NAME  # noqa: E402
except Exception:
    _MY_NAME = ""
from notify import alert            # noqa: E402

DB = BASE / "ab_experiment.db"

# 面试邀约信号（只用于「候选」提醒，不写库）
INTERVIEW_SIGNALS = re.compile(r"面试|面谈|视频面|电话沟通|聊一聊|方便通话|约个时间|面试时间|复试|笔试")

# 系统占位/广告消息（照抄 hr_auto_reply 的判定，不重复踩坑）
SYSTEM_PREFIXES = ("您正在与Boss", "您正在与boss", "您的附件简历")
SYSTEM_CONTAINS = ("撤回了一条消息", "职位竞争者", "查看详细分析")

# ── 遗留招呼语开头（2026-09-20 §3.5）──
# 这些是**改动之前**的招呼语写法。新的招呼语（§3.5 定稿，如「主攻 AI Agent 与
# 自动化工作流落地…」）一句都不匹配它们 —— 也就是说这条规则**不可能**造成
# 「新招呼语被误判成 HR 消息」这个目标故障。留着的唯一用途是认出历史会话里那些
# 没进留痕的老招呼语；等老会话沉底后可以整体删掉。
# 自方消息判定的**主路径**已经是 self_sent 发送留痕（见 _is_my_message）。
_LEGACY_SELF_MARKS = ("您好！我是", "我是")


def _load_reply_corpus() -> tuple:
    """返回 (我发出去过的话, HR 说过的话)。

    数据源是本机的 sent_replies/ 与 archived_replies/ —— 不靠猜，靠留痕。
    ⚠️ 这两个目录里 `message` 字段是 **HR 的原文**，`my_reply` 才是我说的话，
    不能混用（混了会把「HR 的拒绝信」当成「我发的」，判反）。
    """
    mine, theirs = set(), set()
    for d in ("sent_replies", "archived_replies"):
        p = BASE / d
        if not p.exists():
            continue
        for f in p.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            v = (data.get("my_reply") or "").strip()
            if v:
                mine.add(v)
            h = (data.get("message") or "").strip()
            # ⚠️ `message` 是「扫描时该会话的最后一条」，**不一定来自 HR**：
            #  · HR 还没回话时它就是我自己刚发出去的招呼语；
            #  · 也可能是 Boss 的系统占位（「您正在与BossX沟通」）。
            # 两类都要排掉，否则会把招呼语/占位当成 HR 的回复（实测踩过两次）。
            # 自己的招呼语判定：主路径是发送留痕（self_sent），
            # _LEGACY_SELF_MARKS 只兜底改动前发出的老招呼语（见其定义处注释）。
            try:
                import self_sent as _SS
                _is_mine = _SS.is_self_sent(h)
            except Exception:
                _is_mine = False
            if not _is_mine:
                _is_mine = any(h.startswith(k) for k in _LEGACY_SELF_MARKS)
            if h and not (_is_mine
                          or (_MY_NAME and f"我是{_MY_NAME}" in h)
                          or h.startswith(SYSTEM_PREFIXES)):
                theirs.add(h)
    return mine, theirs


def _corpus_hit(m: str, texts: set, min_prefix: int = 8):
    """在留痕语料里找这条消息（返回命中的原文，没命中返回 None）。

    ⚠️ 前缀匹配必须要求**两边都够长**：语料里存在「您好」这种 2 字消息，
    拿它去 startswith 会把所有以「您好」开头的招呼语全判成 HR 消息（实测踩过）。
    """
    if not m:
        return None
    for t in texts:
        if m == t:
            return t
        if len(t) >= min_prefix and len(m) >= min_prefix:
            if m.startswith(t[:min_prefix]) or t.startswith(m[:min_prefix]):
                return t
    return None


def _is_my_message(msg: str, my_texts: set) -> bool:
    """最后一条是不是我发的（招呼语/我的回复/系统占位）——是则不算 HR 回复。

    2026-09-20 §3.5：**主路径改成查发送留痕**（self_sent）。
    原来这里靠 `_self_marks = ["您好！我是", "我是"]` 前缀认自己的话 —— 文案耦合：
    招呼语一改（新文案既不含「您好」也不含「我是」），我们自己发出去的招呼语立刻
    被当成 HR 回复 → 库里写错「HR 已回复」，并且在新扫流程里误触发 REPLY_REVIEW_LOCK
    把投递锁死。换成留痕后，改多少次文案都不会漏判。
    """
    m = (msg or "").strip()
    if not m:
        return False
    # 主路径：这条消息在我们的发送留痕里吗（与文案长什么样无关）
    try:
        import self_sent
        if self_sent.is_self_sent(m, extra=my_texts):
            return True
    except Exception:
        pass
    # 身份信号（与文案无关）：本机档案配了姓名时，「我是<姓名>」必是我方
    if _MY_NAME and f"我是{_MY_NAME}" in m:
        return True
    if any(m.startswith(p) for p in SYSTEM_PREFIXES):
        return True
    # 遗留兜底：只用来认**改动之前**发出去的老招呼语（那些没进留痕）。
    # 这不是判定主路径 —— §3.5 定稿的新招呼语一句都不匹配它，所以它不可能造成
    # 「新招呼语被当成 HR 消息」这个目标故障；等老会话沉底后可整体删除。
    if any(m.startswith(k) for k in _LEGACY_SELF_MARKS):
        return True
    return _corpus_hit(m, my_texts) is not None


def _is_hr_real_message(msg: str, my_texts: set) -> bool:
    """HR 真实消息：不是我自己发的、不是系统占位、不是广告。"""
    m = (msg or "").strip()
    if len(m) < 2:
        return False
    if _is_my_message(m, my_texts):
        return False
    if any(s in m for s in SYSTEM_CONTAINS):
        return False
    return True

# 会话列表读取 JS（选择器来自 hr_auto_reply.py 里已验证的那套）
READ_JS = r"""
(function(){
  function txt(e){ return e ? ((e.textContent||'').trim()) : ''; }
  var out = [];
  document.querySelectorAll("li").forEach(function(li){
    var nb = li.querySelector(".name-box");
    if (!nb) return;
    var status = li.querySelector(".message-status");
    out.push({
      nameBox: txt(nb).slice(0, 60),
      unread: parseInt(txt(li.querySelector(".notice-badge"))) || 0,
      lastMsg: txt(li.querySelector(".last-msg-text")).slice(0, 200),
      time: txt(li.querySelector(".time")),
      readMark: (status && /已读/.test(txt(status))) ? 1 : 0,
      statusCls: (status && (status.className||'').toString()) || ''
    });
  });
  return out;
})()
"""

SCROLL_JS = "window.scrollTo(0, document.body.scrollHeight); 'ok'"


def _target_id(port: int, url_filter: str) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
        targets = json.loads(r.read().decode())
    for t in targets:
        if t.get("type") == "page" and url_filter in (t.get("url") or ""):
            return t["webSocketDebuggerUrl"]
    raise RuntimeError(f"端口 {port} 上没有 {url_filter} 页面（Boss 聊天页没开着）")


def _open_ws(ws_url: str):
    import websocket  # DrissionPage 的依赖，本机已有
    return websocket.create_connection(ws_url, timeout=20, suppress_origin=True)


def _eval_ws(ws, expr: str, msg_id: int = 1):
    ws.send(json.dumps({"id": msg_id, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == msg_id:
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                raise RuntimeError(res["exceptionDetails"].get("text", "JS 执行失败"))
            return res.get("result", {}).get("value")


def collect_conversations(port: int, scrolls: int = 5) -> list:
    """读会话列表（只读：滚动 + 取文本，不点任何东西）。"""
    ws = _open_ws(_target_id(port, "zhipin.com/web/geek/chat"))
    try:
        # 先把列表滚一遍，确保懒加载出来的会话都进了 DOM
        for i in range(scrolls):
            _eval_ws(ws, SCROLL_JS, msg_id=100 + i)
            time.sleep(0.8)
        return _eval_ws(ws, READ_JS, msg_id=1) or []
    finally:
        try:
            ws.close()
        except Exception:
            pass


def company_core(name: str) -> str:
    """公司名去掉常见后缀/前缀，取核心 4+ 个字，用于在 nameBox 里做子串匹配。"""
    s = normalize_company(name or "")
    s = re.sub(r"(有限公司|有限责任公司|股份|集团|科技|技术|信息技术|网络|服务|发展)", "", s)
    return s[:6]


def match_rows(convs: list, days: int) -> tuple:
    """把会话列表与近 N 天的投递记录对上。返回 (命中列表, 未命中的 HR 回复)。"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT application_id, company, company_norm, title, status, read, replied,
                  interview, applied_at
           FROM applications_v2
           WHERE platform='boss' AND applied_at >= ?
             AND status IN ('APPLIED', 'UNCERTAIN', 'VERIFIED')""",
        (cutoff,),
    ).fetchall()
    conn.close()

    matched, seen_ids = [], set()
    for c in convs:
        box = re.sub(r"\s+", "", c.get("nameBox") or "")
        if not box:
            continue
        cands = []
        for r in rows:
            if r["application_id"] in seen_ids:
                continue
            core = company_core(r["company"] or "")
            if len(core) >= 3 and core in box:
                cands.append(r)
        if not cands:
            continue
        # 同公司多条投递时，取最近的一条作为这条会话的主人
        cands.sort(key=lambda r: r["applied_at"] or "", reverse=True)
        row = cands[0]
        seen_ids.add(row["application_id"])
        matched.append((row, c))

    # HR 有回复但没能对上投递记录的：单独列出来，不许静默丢掉
    unmatched_hr = []
    for c in convs:
        if any(c is m[1] for m in matched):
            continue
        msg = c.get("lastMsg") or ""
        if c.get("unread", 0) > 0 or INTERVIEW_SIGNALS.search(msg):
            unmatched_hr.append(c)
    return matched, unmatched_hr


# 我的回复开场词（回复风格定稿是「短版直给」：好的/了解/收到… 起手）
MY_OPENERS = ("好的", "了解", "收到", "谢谢", "感谢", "抱歉", "不好意思", "明白", "可以")

def _classify_last_message(msg: str, unread: int, my_texts: set, hr_texts: set) -> str:
    """判断一条会话的最后一条消息属于谁：'hr' / 'mine' / 'unknown'。

    只认硬特征（用户定稿的判定原则：不确定标 unknown，宁可不写也不写错）：
      · 命中留痕里 HR 说过的话 → hr（最硬）
      · 命中我自己发过的话 / 招呼语 / 系统占位 → mine
      · 未读角标 > 0            → hr（但系统占位要排在其前面，否则会被带偏）
      · 以我的起手词开头         → unknown（可能是我的回复，也可能是 HR 的「好的…」）
      · 其余且有实际内容         → hr
    """
    m = (msg or "").strip()
    if _corpus_hit(m, hr_texts):
        return "hr"
    if m and _is_my_message(m, my_texts):
        return "mine"
    if unread > 0:
        return "hr"
    if not m:
        return "unknown"
    if m.startswith(MY_OPENERS):
        return "unknown"
    if len(m) < 3:
        return "unknown"
    return "hr"


def plan_updates(matched: list, my_texts: set, hr_texts: set) -> tuple:
    """算出需要写的字段（只把 0 翻成 1）。返回 (updates, unknowns)。"""
    out, unknowns = [], []
    for row, conv in matched:
        new_read = 1 if (conv.get("readMark") or row["read"]) else 0
        kind = _classify_last_message(conv.get("lastMsg") or "", conv.get("unread", 0) or 0,
                                      my_texts, hr_texts)
        if kind == "unknown":
            unknowns.append((row, conv))
        new_replied = 1 if (kind == "hr" or row["replied"]) else 0
        if new_read != row["read"] or new_replied != row["replied"]:
            out.append((row, new_read, new_replied, conv))
    return out, unknowns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9223)
    ap.add_argument("--days", type=int, default=30, help="只匹配最近 N 天的投递记录")
    ap.add_argument("--apply", action="store_true", help="写库（默认只预演）")
    args = ap.parse_args()

    try:
        convs = collect_conversations(args.port)
    except Exception as e:
        print(f"❌ 读取聊天列表失败：{e}")
        print("   （需要 Chrome 开着调试端口，且 Boss 聊天页已登录）")
        return 2

    print(f"📋 读到 {len(convs)} 条会话")
    my_texts, hr_texts = _load_reply_corpus()
    print(f"📝 留痕语料：我发过 {len(my_texts)} 条 · HR 说过 {len(hr_texts)} 条"
          "（用来区分「HR 回复」和「我自己发的」）")
    matched, unmatched_hr = match_rows(convs, args.days)
    print(f"🧩 对上投递记录 {len(matched)} 条；HR 有动静但对不上记录的 {len(unmatched_hr)} 条")

    updates, unknowns = plan_updates(matched, my_texts, hr_texts)
    read_flips = [u for u in updates if u[1] and not u[0]["read"]]
    reply_flips = [u for u in updates if u[2] and not u[0]["replied"]]
    interviews = [c for c in convs if INTERVIEW_SIGNALS.search(c.get("lastMsg") or "")]

    print(f"🔁 状态可订正 {len(updates)} 条（新增已读 {len(read_flips)} · 新增回复 {len(reply_flips)}）")
    for u in reply_flips[:8]:
        print(f"   💬 回复: {u[0]['company'][:18]} | {u[0]['title'][:20]} ← {(u[3].get('lastMsg') or '')[:40]}")
    if unknowns:
        # 「不确定」不写库、不告警，只列出来给你瞄一眼（用户定稿的判定原则）
        print(f"❓ 判不准是谁发的（不写库）{len(unknowns)} 条：")
        for row, conv in unknowns[:6]:
            print(f"   · {row['company'][:16]} ← {(conv.get('lastMsg') or '')[:40]}")
    if interviews:
        print(f"🎯 疑似面试邀约 {len(interviews)} 条（只提醒，不自动写库）：")
        for c in interviews[:6]:
            print(f"   · {c['nameBox'][:26]} | {(c.get('lastMsg') or '')[:50]}")

    if not args.apply:
        print("\n这是预演（dry-run）。确认无误后加 --apply 写库。")
        return 0

    conn = sqlite3.connect(str(DB))
    now = datetime.now().isoformat()
    for row, new_read, new_replied, _conv in updates:
        conn.execute(
            "UPDATE applications_v2 SET read=?, replied=?, updated_at=? WHERE application_id=?",
            (new_read, new_replied, now, row["application_id"]),
        )
    conn.commit()
    conn.close()
    print(f"✅ 已回填 {len(updates)} 条（只翻了 0→1，没有回退任何状态）")

    if reply_flips or interviews:
        lines = []
        if reply_flips:
            lines.append(f"**HR 新回复 {len(reply_flips)} 条**")
            for u in reply_flips[:6]:
                lines.append(f"· {u[0]['company'][:16]}：{(u[3].get('lastMsg') or '')[:50]}")
        if interviews:
            lines.append(f"\n**疑似面试邀约 {len(interviews)} 条**")
            for c in interviews[:5]:
                lines.append(f"· {c['nameBox'][:22]}：{(c.get('lastMsg') or '')[:50]}")
        lines.append("\n去 Boss 聊一句（缺口在等你回话）。")
        alert("hr_replies", f"HR 有 {len(reply_flips)} 条新回复、{len(interviews)} 条面试线索",
              "\n".join(lines), level="info", throttle=3600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

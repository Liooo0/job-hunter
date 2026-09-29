#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：找出「HR 回了话」的会话。

原理：Boss 聊天列表每条 li 的文本形如
    「昨天 陈先生慧博云通招聘专员 [已读] <最后一条消息预览>」
预览如果是我们发出去的草稿 → 还没回；不是我们的草稿 → HR 有新回复。
用队列里已发草稿做对照，不靠猜。

用法: python3 scripts/check_new_replies.py
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab, read_via_ws  # noqa: E402


def js_safe(ws_url, expr, tries=3, timeout=20):
    """读 JS，连接断了就重建（实测同一连接多次 evaluate+滚动后会
    WebSocketConnectionClosedException，一次失败不该让整轮报销）。"""
    for i in range(tries):
        try:
            return read_via_ws(ws_url, expr, timeout=timeout)
        except Exception as e:
            if i == tries - 1:
                print(f"   ⚠️ 读取失败（已重试 {tries} 次）: {type(e).__name__}")
                return None
            time.sleep(1.5)
    return None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

READ_ALL = r"""
(() => {
  const out = [];
  for (const li of document.querySelectorAll('li')) {
    const t = (li.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.length > 12 && /女士|先生|HR|招聘|职位|猎头/.test(t)) out.push(t.slice(0, 160));
  }
  return JSON.stringify(out);
})()
"""

SCROLL = r"""
(() => {
  for (const el of document.querySelectorAll('ul,div')) {
    if (el.scrollHeight > el.clientHeight + 50) { el.scrollTop += 900; return 'SCROLLED'; }
  }
  return 'NO_SCROLL';
})()
"""

# 我发出去的草稿（用于判断预览是不是我们自己发的）
drafts = []
try:
    for s in json.load(open(os.path.join(ROOT, "data", "reply_pending.json"), encoding="utf-8")):
        if s.get("status") == "sent" and s.get("draft"):
            drafts.append(re.sub(r"\s+", "", s["draft"]))
except Exception as e:
    print("读队列失败:", e)

tab = open_tab("https://www.zhipin.com/web/geek/chat")
tid, ws = tab.get("id"), tab.get("webSocketDebuggerUrl")
seen = set()
rows = []
try:
    time.sleep(9)
    for _ in range(6):                      # 边滚边收
        raw = js_safe(ws, READ_ALL, timeout=25)
        try:
            got = json.loads(raw or "[]")
        except Exception:
            got = []
        for t in got:
            if t not in seen:
                seen.add(t)
                rows.append(t)
        js_safe(ws, SCROLL, timeout=15)
        time.sleep(1.5)
finally:
    close_tab(tid)

print(f"共读到 {len(rows)} 条会话行\n")
mine = other = 0
for t in rows:
    flat = re.sub(r"\s+", "", t)
    is_mine = any(d[:24] and d[:24] in flat for d in drafts)
    tail = t[-60:]
    if is_mine:
        mine += 1
    else:
        other += 1
        if not re.search(r"职位:|\[已读\]$|^0", t):
            print(f"  ❓ 待人工看: {t[:120]}")

print(f"\n统计：预览是我们草稿的 {mine} 条（未回），其它 {other} 条")
print("（注：列表为虚拟滚动，未滚到的会话不在此列）")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读抽验：搜索目标 HR，读会话预览（预览=最后一条消息 → 能证明我们发的已送达）

注意两套 JS 约定别混：
  · CDP Runtime.evaluate（cdp51.read_via_ws）→ 用裸表达式 (() => {...})()
  · DrissionPage run_js → 必须 return (() => {...})()
"""
import json, sys, time
sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab, read_via_ws

SET_JS = r"""
(() => {
  const b = document.querySelector('input.boss-search-input');
  if (!b) return 'NO_BOX';
  const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  s.call(b, %s);
  b.dispatchEvent(new Event('input', {bubbles: true}));
  return 'SET:' + b.value;
})()
"""

READ_JS = r"""
(() => {
  const out = [];
  for (const li of document.querySelectorAll('li')) {
    const t = (li.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.length > 12 && /女士|先生|HR|招聘|职位/.test(t)) out.push(t.slice(0, 115));
  }
  return JSON.stringify(out.slice(0, 3));
})()
"""

tab = open_tab("https://www.zhipin.com/web/geek/chat")
tid, ws = tab.get("id"), tab.get("webSocketDebuggerUrl")
try:
    time.sleep(9)
    for kw in ("慧博云通", "卓越际联", "抬头悟", "阿里巴巴"):
        r = read_via_ws(ws, SET_JS % json.dumps(kw, ensure_ascii=False), timeout=20)
        time.sleep(4)
        rows = json.loads(read_via_ws(ws, READ_JS, timeout=25) or "[]")
        print(f"\n── 搜索「{kw}」→ {r}")
        for x in rows:
            print(f"   {x}")
finally:
    close_tab(tid)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：Boss 聊天页搜索框能否定位待回复的会话"""
import json
import sys
import time

sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab, read_via_ws  # noqa: E402

DUMP = r"""
(() => {
  const q = "%s";
  const box = document.querySelector('input.boss-search-input') ||
              [...document.querySelectorAll('input')].find(i => (i.placeholder||'').includes('搜索'));
  if (!box) return JSON.stringify({err: 'no-search-box'});
  box.focus();
  box.value = q;
  box.dispatchEvent(new Event('input', {bubbles: true}));
  const items = [...document.querySelectorAll('li')]
      .map(l => (l.innerText||'').replace(/\s+/g,' ').trim())
      .filter(t => t.length > 8);
  return JSON.stringify({query: q, box: box.placeholder,
                         count: items.length, sample: items.slice(0, 10)});
})()
"""

tab = open_tab("https://www.zhipin.com/web/geek/chat")
tid, ws = tab.get("id"), tab.get("webSocketDebuggerUrl")
try:
    time.sleep(7)
    base = json.loads(read_via_ws(ws, DUMP % "" , timeout=25) or "{}")
    print(f"搜索框: {base.get('box')} | 初始条目 {base.get('count')}")
    for kw in ("慧博", "陈先生", "李女士", "聚客"):
        d = json.loads(read_via_ws(ws, DUMP % kw, timeout=25) or "{}")
        time.sleep(3.5)
        print(f"\n── 搜索「{kw}」→ 过滤后 {d.get('count')} 条")
        for s in (d.get("sample") or [])[:6]:
            print(f"     {s[:70]}")
finally:
    close_tab(tid)

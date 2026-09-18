#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探 51job 是否有一个可读的站内聊天页（只读）"""
import json, sys, time
sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab, read_via_ws

JS = r"""
(() => {
  const t = (document.body.innerText || '').replace(/\s+/g,' ');
  return JSON.stringify({
    text: t.slice(0, 800),
    html_len: (document.body.innerHTML || '').length,
    has_input: !!document.querySelector('textarea,input[type=text]'),
    appish: document.querySelectorAll('[class*=chat],[class*=msg],[class*=message],[class*=im-]').length,
    title: document.title
  });
})()
"""

for url in ("https://we.51job.com/pc/message", "https://we.51job.com/pc/chat"):
    tab = open_tab(url)
    tid, ws = tab.get("id"), tab.get("webSocketDebuggerUrl")
    print(f"\n═══ {url} ═══")
    try:
        for i in range(14):                     # 多等一会儿（SPA 慢）
            time.sleep(3)
            d = json.loads(read_via_ws(ws, JS, timeout=25) or "{}")
            if d.get("text") or i >= 12:
                break
        print(f"  标题: {d.get('title')!r}  html长度: {d.get('html_len')}")
        print(f"  有输入框: {d.get('has_input')}  聊天类元素: {d.get('appish')}")
        print(f"  文字: {d.get('text','')[:400]}")
    finally:
        close_tab(tid)

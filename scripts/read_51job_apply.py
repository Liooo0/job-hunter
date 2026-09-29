#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读 51job 投递记录页，找 HR 回复/沟通状态（只读）。"""
import json
import sys
import time

sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab, read_via_ws  # noqa: E402

JS = r"""
(() => {
  const txt = (document.body.innerText || '').replace(/\s+/g, ' ');
  const links = [];
  document.querySelectorAll('a').forEach(a => {
    const t = (a.textContent || '').trim();
    if (t && t.length < 30) links.push(t + ' => ' + (a.href || '').slice(0, 100));
  });
  return JSON.stringify({
    url: location.href, title: document.title, len: document.body.innerHTML.length,
    text: txt.slice(0, 3000),
    links: [...new Set(links)].slice(0, 40)
  });
})()
"""

for url in ("https://i.51job.com/userset/my_apply.php",):
    tab = open_tab(url)
    ws = tab.get("webSocketDebuggerUrl")
    print(f"═══ {url} ═══")
    d = {}
    for i in range(14):
        time.sleep(3)
        d = json.loads(read_via_ws(ws, JS, timeout=25) or "{}")
        if d.get("text") and len(d["text"]) > 200:
            break
    print("实际URL:", d.get("url"), "| 标题:", (d.get("title") or "").strip()[:50])
    print("\n--- 正文 ---")
    print(d.get("text") or "(空)")
    print("\n--- 链接 ---")
    for l in (d.get("links") or [])[:25]:
        print("  ·", l)
    try:
        close_tab(tab.get("id"))
    except Exception:
        pass

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 51job 搜索页（自动化在用的、确认能打开的页面）里挖出个人中心各入口。
只读。
"""
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab, read_via_ws  # noqa: E402

JS = r"""
(() => {
  const out = {hrefs: [], nav: [], texts: []};
  // 1) 所有含关键词的链接
  document.querySelectorAll('a').forEach(a => {
    const t = (a.innerText || '').trim();
    const h = a.href || '';
    if (!h) return;
    if (/简历|投递|申请|消息|沟通|通知|我的|个人|账号/.test(t + h)) {
      out.hrefs.push(t.slice(0, 18) + ' → ' + h.slice(0, 110));
    }
  });
  // 2) 头部导航区域的文字（SPA 常把入口做成 div 而非 a）
  ['header', 'nav', '.header', '.nav', '.top'].forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
      if (t) out.nav.push(sel + ': ' + t.slice(0, 260));
    });
  });
  // 3) 整页文字里的「消息/未读」上下文
  const all = (document.body.innerText || '').replace(/\s+/g, ' ');
  ['消息', '未读', '投递记录', '我的简历', '沟通'].forEach(kw => {
    const i = all.indexOf(kw);
    if (i >= 0) out.texts.push(kw + ': …' + all.slice(Math.max(0, i - 60), i + 90) + '…');
  });
  out.bodyLen = all.length;
  return JSON.stringify(out);
})()
"""

url = sys.argv[1] if len(sys.argv) > 1 else "https://we.51job.com/pc/search?keyword=AI%E5%BA%94%E7%94%A8&jobArea=040000"
tab = open_tab(url)
tid, ws = tab.get("id"), tab.get("webSocketDebuggerUrl")
try:
    for i in range(10):
        time.sleep(2)
        href = read_via_ws(ws, "location.href") or ""
        if "51job" in str(href) and i >= 2:
            break
    print(f"实际地址: {str(read_via_ws(ws, 'location.href'))[:110]}")
    raw = read_via_ws(ws, JS, timeout=25)
    d = json.loads(raw) if raw else {}
    print(f"页面文字长度: {d.get('bodyLen')}")
    print("\n── 导航区文字 ──")
    for n in (d.get("nav") or [])[:6]:
        print("  ", n)
    print("\n── 个人中心类链接 ──")
    seen = set()
    for h in (d.get("hrefs") or []):
        if h in seen:
            continue
        seen.add(h)
        print("  ", h)
        if len(seen) >= 22:
            break
    print("\n── 关键词上下文 ──")
    for t in (d.get("texts") or []):
        print("  ", t)
finally:
    close_tab(tid)

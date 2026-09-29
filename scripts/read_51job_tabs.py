#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读 51job 申请页的「已查阅 / 感兴趣 / 邀面试」分栏（只读，仅点击筛选标签）。"""
import json
import sys
import time

sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab, read_via_ws  # noqa: E402

CLICK = r"""
(() => {
  const want = "%s";
  const els = document.querySelectorAll('li,a,span,div,button');
  for (const el of els) {
    const t = (el.textContent || '').trim();
    if (t === want || t.startsWith(want)) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { el.click(); return 'clicked:' + t; }
    }
  }
  return 'not_found';
})()
"""

READ = r"""
(() => {
  const txt = (document.body.innerText || '').replace(/\s+/g, ' ');
  const i = txt.indexOf('社会申请');
  const seg = i >= 0 ? txt.slice(i, i + 2600) : txt.slice(0, 2600);
  return JSON.stringify({ url: location.href, seg: seg });
})()
"""

TABS = ["感兴趣", "邀面试", "已查阅"]

tab = open_tab("https://i.51job.com/userset/my_apply.php?lang=c")
ws = tab.get("webSocketDebuggerUrl")
time.sleep(10)

for name in TABS:
    r = read_via_ws(ws, CLICK % name, timeout=25)
    print(f"\n{'='*58}\n【{name}】 → {r}")
    time.sleep(7)
    d = json.loads(read_via_ws(ws, READ, timeout=30) or "{}")
    seg = d.get("seg") or ""
    # 裁掉页脚
    for cut in ("下载 APP 意见 反馈", "销售热线", "上一页 1 2 3"):
        j = seg.find(cut)
        if j > 0:
            seg = seg[:j]
    print(seg[:1800])

try:
    close_tab(tab.get("id"))
except Exception:
    pass

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出求职意向编辑表单里所有 el-select 的当前渲染值（只读，用来字段对号）"""
import json
import sys
import time

sys.path.insert(0, ".")
from DrissionPage import ChromiumOptions, ChromiumPage  # noqa: E402

JS = r"""
(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.height > 0 && r.width > 0; };
  const out = {selects: [], radios: [], texts: []};
  document.querySelectorAll('.el-select').forEach((s, i) => {
    if (!vis(s)) return;
    const t = (s.innerText || '').replace(/\s+/g, ' ').trim();
    const r = s.getBoundingClientRect();
    out.selects.push({i: i, txt: t.slice(0, 40), x: Math.round(r.left + r.width / 2),
                      y: Math.round(r.top + r.height / 2)});
  });
  document.querySelectorAll('.el-radio-button, .el-radio').forEach(r0 => {
    if (!vis(r0)) return;
    const t = (r0.innerText || '').trim();
    const cls = (r0.className || '').toString();
    if (t) out.radios.push({txt: t.slice(0, 10), checked: /is-active|is-checked/.test(cls)});
  });
  // 表单区域的整段文字（含标签）
  const all = (document.body.innerText || '').replace(/\s+/g, ' ');
  ['期望职位', '期望城市', '期望薪资', '期望行业', '求职意向'].forEach(k => {
    const i = all.indexOf(k);
    if (i >= 0) out.texts.push(all.slice(i, i + 90));
  });
  return JSON.stringify(out);
})()
"""

page = ChromiumPage(ChromiumOptions().set_local_port(9223))
tab = page.new_tab("https://www.51job.com/resume/center")
try:
    time.sleep(11)
    box = tab.ele("css:div.careerObjectiveSet", timeout=6)
    box.hover()
    time.sleep(2)
    for sel in ("css:div.content_editclick", "css:div.content_title_edit"):
        e = box.ele(sel, timeout=3)
        if e is not None:
            e.click()
            break
    time.sleep(7)
    d = json.loads(tab.run_js("return " + JS) or "{}")
    print("可见 el-select（按渲染值识别字段）：")
    for s in (d.get("selects") or []):
        print("   #%s 值=%-26s @%s,%s" % (s.get("i"), s.get("txt"), s.get("x"), s.get("y")))
    print("")
    print("单选（全职/兼职）：", d.get("radios"))
    print("")
    print("表单标签附近的文字：")
    for t in (d.get("texts") or []):
        print("   ", t[:88])
finally:
    pass

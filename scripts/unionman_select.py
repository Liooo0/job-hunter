#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""九联补充表 —— 稳妥版 el-select 选择器。

2026-09-15 踩坑：Element UI 的下拉 popper 默认渲染在 body 下、祖先带 display:none，
用 `.el-select-dropdown:not([style*="display: none"])` 过滤会抓错容器（点了别项的下拉）。
正确做法：按**可见性**(getBoundingClientRect().height>0) 判定，且只点当前已展开的那一个。
"""
import json
import time

from DrissionPage import ChromiumPage

PORT = 9224
page = ChromiumPage(PORT)
tab = next((t for t in page.get_tabs() if 'unionman' in (t.url or '')), None)
if tab is None:
    raise SystemExit('❌ 没找到 unionman 标签页')

OPEN_JS = r"""return (() => {
  const items = document.querySelectorAll('.el-form-item');
  for (const it of items) {
    const l = it.querySelector('.el-form-item__label');
    if (l && l.innerText.trim() === '%s') {
      const inp = it.querySelector('.el-select input, input');
      if (!inp) return 'no-input';
      inp.focus(); inp.click();
      return 'opened';
    }
  }
  return 'label-not-found';
})()"""

# 只点「可见」且文字精确匹配的选项
PICK_JS = r"""return (() => {
  const vis = (e) => { try { const r = e.getBoundingClientRect(); return r.height > 0 && r.width > 0; } catch (x) { return false; } };
  const opts = [...document.querySelectorAll('.el-select-dropdown__item')].filter(vis);
  const labels = opts.map(o => (o.innerText || '').trim());
  for (const o of opts) {
    if ((o.innerText || '').trim() === '%s') { o.click(); return 'picked:' + o.innerText.trim(); }
  }
  return 'visible-options=' + labels.slice(0, 20).join('|');
})()"""

READ_JS = r"""return (() => {
  const out = [];
  document.querySelectorAll('.el-form-item').forEach((it) => {
    const l = it.querySelector('.el-form-item__label');
    const lab = l ? (l.innerText || '').trim() : '';
    const inp = it.querySelector('input, textarea');
    const v = inp ? String(inp.value || '') : '';
    const req = (it.className || '').indexOf('is-required') >= 0 ||
                (l && (l.className || '').indexOf('is-required') >= 0);
    out.push([lab, v, req ? 'REQ' : ''].join(' ~ '));
  });
  return out.join('\n');
})()"""


def set_select(label, value):
    st = tab.run_js(OPEN_JS % label)
    time.sleep(1.0)
    picked = tab.run_js(PICK_JS % value)
    time.sleep(0.6)
    return st, picked


if __name__ == '__main__':
    import sys
    targets = [('性别', '男'), ('学历', '本科')]
    if len(sys.argv) > 1:          # 允许临时传入 标签=值 对
        targets = [(a.split('=', 1)[0], a.split('=', 1)[1]) for a in sys.argv[1:]]
    for lab, val in targets:
        st, pk = set_select(lab, val)
        print(f'  {lab} = {val} -> {st} / {pk}')
    print('\n=== 复核 ===')
    print(tab.run_js(READ_JS))

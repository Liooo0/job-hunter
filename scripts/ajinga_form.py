#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读菁客申请表单的字段：标签 + 实时值 + 是否必填 + 是否报错。"""
from DrissionPage import ChromiumPage
import sys

PORT = 9224
JS = r"""return (() => {
  const out = [];
  document.querySelectorAll('.el-form-item').forEach((it, i) => {
    const labEl = it.querySelector('.el-form-item__label');
    const lab = labEl ? (labEl.innerText || '').trim() : '';
    const inp = it.querySelector('input, textarea');
    const val = inp ? String(inp.value || '') : '';
    const req = it.className.indexOf('is-required') >= 0 ||
                (labEl && labEl.className.indexOf('is-required') >= 0);
    const err = it.querySelector('.el-form-item__error');
    out.push([i, lab, val, req ? 'REQ' : '', err ? ('ERR:' + (err.innerText || '')) : ''].join(' ~ '));
  });
  const sel = [];
  document.querySelectorAll('.el-select').forEach((s, i) => {
    const t = s.innerText || '';
    sel.push(i + ' ~ ' + t.trim().replace(/\\s+/g, ' ').slice(0, 40));
  });
  return '=== FORM (' + out.length + ' items) ===\n' + out.join('\n') +
         '\n=== SELECTS ===\n' + sel.join('\n');
})()"""

page = ChromiumPage(PORT)
tabs = [x for x in page.get_tabs() if 'ajinga' in (x.url or '')]
if not tabs:
    raise SystemExit('❌ 没有 ajinga 标签')
tab = tabs[0]
print('URL:', tab.url[:120])
print(tab.run_js(JS))

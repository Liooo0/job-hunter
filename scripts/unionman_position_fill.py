#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""填九联「应聘岗位（第一/第二志愿）」。

它是 el-input 文本输入 + 应该是联想选择（placeholder 写"请选择应聘岗位"）。
所以必须：focus → 输入 → 等联想列表 → **点中列表项**（直接塞 value 可能不被 Vue 接受）。
"""
import sys
import time

from DrissionPage import ChromiumPage

p = ChromiumPage(9224)
tab = next(x for x in p.get_tabs() if 'unionman' in (x.url or ''))

FOCUS = """return (() => {
  for (const it of document.querySelectorAll('.el-form-item')) {
    const l = it.querySelector('.el-form-item__label');
    if (l && l.innerText.indexOf('%s') >= 0) {
      const i = it.querySelector('input');
      if (!i) return 'no-input';
      i.focus(); i.click(); return 'focused';
    }
  }
  return 'not-found';
})()"""

VAL = """return (() => {
  for (const it of document.querySelectorAll('.el-form-item')) {
    const l = it.querySelector('.el-form-item__label');
    if (l && l.innerText.indexOf('%s') >= 0) {
      const i = it.querySelector('input');
      return i ? String(i.value || '') : '?';
    }
  }
  return '?';
})()"""

# 联想列表：Element UI 的 el-autocomplete / el-select 远程搜索，可见项在 body 下的 popper 里
PICK = """return (() => {
  const vis = (e) => { try { const r = e.getBoundingClientRect(); return r.height > 0; } catch (x) { return false; } };
  const cands = [...document.querySelectorAll(
    '.el-autocomplete-suggestion li, .el-select-dropdown__item, .el-autocomplete-suggestion__wrap li, li')].filter(vis);
  const labels = cands.map(e => (e.innerText || '').trim()).filter(Boolean);
  for (const c of cands) {
    const t = (c.innerText || '').trim();
    if (t === '%s' || t.indexOf('%s') >= 0) { c.click(); return 'picked:' + t; }
  }
  return labels.length ? 'visible=' + labels.slice(0, 10).join('|') : 'no-suggestion';
})()"""


def fill(label, value):
    st = tab.run_js(FOCUS % label)
    time.sleep(0.6)
    if st != 'focused':
        return st, ''
    tab.run_cdp('Input.insertText', text=value)
    time.sleep(1.5)
    picked = tab.run_js(PICK % (value, value))
    time.sleep(0.8)
    now = tab.run_js(VAL % label)
    return picked, now


if __name__ == '__main__':
    pairs = [(a.split('=', 1)[0], a.split('=', 1)[1]) for a in sys.argv[1:]]
    for lab, val in pairs:
        pk, now = fill(lab, val)
        print(f'  {lab} <- {val}\n     pick={pk}\n     现值={now!r}')

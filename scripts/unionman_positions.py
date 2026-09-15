#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读九联表「应聘岗位」下拉的真实可选项（用户不清楚自己投的是哪个岗）。"""
import time

from DrissionPage import ChromiumPage

p = ChromiumPage(9224)
tab = next(x for x in p.get_tabs() if 'unionman' in (x.url or ''))

OPEN = """return (() => {
  for (const it of document.querySelectorAll('.el-form-item')) {
    const l = it.querySelector('.el-form-item__label');
    if (l && l.innerText.indexOf('%s') >= 0) {
      const i = it.querySelector('input');
      if (!i) return 'no-input';
      i.focus(); i.click(); return 'opened';
    }
  }
  return 'not-found';
})()"""

VIS = """return (() => {
  const vis = (e) => { try { return e.getBoundingClientRect().height > 0; } catch (x) { return false; } };
  const o = [...document.querySelectorAll('.el-select-dropdown__item')].filter(vis)
              .map(e => (e.innerText || '').trim());
  return o.length ? o.join(' / ') : '(空或未展开)';
})()"""

for label in ('第一志愿', '第二志愿'):
    st = tab.run_js(OPEN % label)
    time.sleep(1.5)
    opts = tab.run_js(VIS)
    print(f'【{label}】{st}')
    print(f'  {opts[:800]}')
    tab.run_js("return document.body.click(), 'x';")
    time.sleep(0.8)

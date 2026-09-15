#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""9224 独立 Chrome 上走菁客(AJINGA)申请补全 —— 状态读取。"""
import re
import sys
import time

from DrissionPage import ChromiumPage

URL = sys.argv[1]
PORT = 9224

page = ChromiumPage(PORT)
try:
    tabs = [t for t in page.get_tabs() if t.url and 'ajinga' in (t.url or '')]
except Exception:
    tabs = []
if tabs:
    tab = tabs[0]
    print('复用已有 ajinga 标签')
else:
    tab = page.new_tab(URL)
time.sleep(7)

print('URL  :', tab.url[:130])
print('标题 :', tab.title[:90])
txt = tab.ele('tag:body').text if hasattr(tab, 'ele') else ''
print('--- 正文（前 1600 字）---')
print(txt[:1600])
print('--- 交互元素 ---')
for sel, label in [('tag:button', 'button'), ('tag:input', 'input'),
                   ('css:.el-checkbox', 'checkbox'), ('tag:a', 'a')]:
    try:
        els = tab.eles(sel)
    except Exception as e:
        print(f'  {label}: 异常 {e}')
        continue
    if not els:
        continue
    print(f'  {label} 共 {len(els)}:')
    for e in els[:14]:
        try:
            t = (e.text or '').strip().replace('\n', ' ')[:40]
        except Exception:
            t = ''
        attrs = []
        for a in ('type', 'placeholder', 'class', 'href'):
            try:
                v = e.attr(a)
                if v:
                    attrs.append(f'{a}={str(v)[:52]}')
            except Exception:
                pass
        if t or attrs:
            print(f'    {t!r:44} {" ".join(attrs)[:120]}')

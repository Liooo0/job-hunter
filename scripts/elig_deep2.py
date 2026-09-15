#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""深挖两家：百川智能「源点顶尖人才计划」的招聘对象 / 民生银行校招的毕业时间要求。"""
import re
import time

from DrissionPage import ChromiumPage

PORT = 9223
KWS = ['择业期', '毕业时间', '应届', '往届', '届', '2026', '2027', '2025', '学历', '要求']

page = ChromiumPage(PORT)

# ── 1) 百川智能：找「源点计划」入口并打开 ──
print('=' * 76)
print('■ 百川智能 源点计划')
tab = page.new_tab('https://careers.baichuan-inc.com/')
time.sleep(10)
link = tab.run_js(r"""return (() => {
  const a = [...document.querySelectorAll('a')].filter(x => /源点|顶尖人才/.test(x.innerText || ''));
  if (a.length) return a[0].href || '';
  const any = [...document.querySelectorAll('*')].filter(x => x.children.length === 0 && /源点顶尖人才计划/.test(x.innerText || ''));
  return any.length ? 'TEXT_ONLY' : '';
})()""")
print('   入口:', link)
if link and link.startswith('http'):
    t2 = page.new_tab(link)
    time.sleep(10)
    b = t2.ele('tag:body').text or ''
    print('   标题:', (t2.title or '')[:60], '| 正文', len(b), '字')
    for line in re.split(r'[\n\r]+', b):
        s = line.strip()
        if len(s) > 3 and any(k in s for k in KWS):
            print('    ·', s[:170])
    t2.close()
else:
    b = tab.ele('tag:body').text or ''
    print('   站内正文:', b[:600].replace('\n', ' | '))
tab.close()

# ── 2) 民生银行：校园招聘 ──
print('=' * 76)
print('■ 民生银行 校园招聘')
tab = page.new_tab('http://career.cmbc.com.cn:8080/index.jsp#/app/recruitment/campus')
time.sleep(12)
b = tab.ele('tag:body').text or ''
print('   标题:', (tab.title or '')[:60], '| 正文', len(b), '字')
for line in re.split(r'[\n\r]+', b):
    s = line.strip()
    if len(s) > 4 and any(k in s for k in KWS):
        print('    ·', s[:170])
# 尝试点进校招公告
res = tab.run_js(r"""return (() => {
  const a = [...document.querySelectorAll('a,li,div')].filter(x => x.children.length === 0 &&
    /校园招聘公告|招聘公告|校园招聘启事|2027/.test(x.innerText || ''));
  return a.length ? a.map(x => (x.innerText||'').trim()).slice(0,6).join(' / ') : '';
})()""")
print('   可疑入口:', res or '(无)')
tab.close()

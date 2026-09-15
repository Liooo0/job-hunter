#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浏览器批量核招聘站：百川智能 / 广联达(Moka) / 民生银行。

Moka(app.mokahr.com) 与多数国企招聘站是纯 JS SPA，curl 只有空壳，必须渲染。
"""
import re
import time

from DrissionPage import ChromiumPage

PORT = 9223
KWS = ['择业期', '毕业时间', '应届', '往届', '校园招聘', '校招', '实习', '届毕业生', '2026', '2027', '2025']

TARGETS = [
    ("百川智能-校招", "https://careers.baichuan-inc.com/"),
    ("广联达-Moka", "https://app.mokahr.com/apply/glodon/1751"),
    ("民生银行-校招", "http://career.cmbc.com.cn:8080/index.jsp#/app/recruitment/campus"),
]

page = ChromiumPage(PORT)
for name, url in TARGETS:
    print('=' * 76)
    print(f'■ {name}  {url}')
    try:
        tab = page.new_tab(url)
        time.sleep(12)
        print('   标题:', (tab.title or '')[:70])
        body = tab.ele('tag:body').text or ''
        print('   正文', len(body), '字')
        hits = []
        for line in re.split(r'[\n\r]+', body):
            s = line.strip()
            if len(s) < 3:
                continue
            if any(k in s for k in KWS) and s not in hits:
                hits.append(s)
            if len(hits) >= 14:
                break
        for h in hits:
            print('    ·', h[:150])
        if not hits:
            print('    前 400 字:', body[:400].replace('\n', ' | '))
        # 找校招/职位列表相关链接或标签
        extra = tab.run_js(r"""return (() => {
          const a = [...document.querySelectorAll('a,button,li,div')]
            .filter(x => (x.children.length === 0) && /校招|校园|职位|岗位|应届|源点|社会招聘/i.test(x.innerText||''));
          return [...new Set(a.map(x => (x.innerText||'').trim()).filter(Boolean))].slice(0, 10).join(' / ');
        })()""")
        if extra:
            print('   标签:', str(extra)[:300])
        tab.close()
    except Exception as e:
        print('   ❌', str(e)[:110])
    time.sleep(1)

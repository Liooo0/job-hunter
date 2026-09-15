#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量资格核查：打开企业官方招聘页，抽「招聘对象/毕业时间/择业期」相关段落。

为什么用浏览器：多数国企/银行/民企招聘站是 JS SPA（curl 只能拿到空壳），
且部分有 WAF（403）。
用法： python3 elig_check.py
"""
import re
import time

from DrissionPage import ChromiumPage

PORT = 9223
KWS = ['择业期', '毕业时间', '应届', '往届', '校园招聘', '校招', '届毕业生', '报名条件', '招聘对象', '学历要求']

TARGETS = [
    ("百川智能", "https://www.baichuan-ai.com/"),
    ("浦发银行招聘", "https://job.spdb.com.cn/"),
    ("浦发银行公告", "https://job.spdb.com.cn/notices"),
    ("广联达招聘", "https://www.glodon.com/"),
    ("中国邮政招聘", "https://www.chinapost.com.cn/html1/category/1813/8108-1.htm"),
    ("汇丰中国校招", "https://www.hsbc.com.cn/careers/"),
]

page = ChromiumPage(PORT)

for name, url in TARGETS:
    print('=' * 76)
    print(f'■ {name}  {url}')
    try:
        tab = page.new_tab(url)
        time.sleep(9)
        body = tab.ele('tag:body').text or ''
        print(f'   标题: {(tab.title or "")[:70]} | 正文 {len(body)} 字')
        hits = []
        for line in re.split(r'[\n\r]+', body):
            s = line.strip()
            if len(s) < 6:
                continue
            if any(k in s for k in KWS) and s not in hits:
                hits.append(s)
            if len(hits) >= 8:
                break
        if hits:
            for h in hits:
                print('   ·', h[:170])
        else:
            print('   (无关键命中) 前 200 字:', body[:200].replace('\n', ' '))
        # 顺便找招聘/校招相关链接
        links = tab.run_js(r"""return (() => {
          const a = [...document.querySelectorAll('a')].filter(x => /招聘|校招|校园|加入我们|careers|job/i.test((x.innerText||'') + (x.href||'')));
          return [...new Set(a.map(x => (x.innerText||'').trim() + ' -> ' + x.href))].slice(0, 6).join('\n');
        })()""")
        if links:
            print('   链接:')
            for l in str(links).split('\n')[:6]:
                print('     ', l[:150])
        tab.close()
    except Exception as e:
        print('   ❌ 异常:', str(e)[:120])
    time.sleep(1)

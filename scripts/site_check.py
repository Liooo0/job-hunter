#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""访问官网并抓取与「校招/届别/择业期」相关的片段（web_search/web_extract 后端太差时的兜底）。

用法： python3 site_check.py <url> [port] [关键词1,关键词2,...]
"""
import re
import sys
import time

from DrissionPage import ChromiumPage

url = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 9223
kws = (sys.argv[3].split(',') if len(sys.argv) > 3
       else ['届', '校招', '校园招聘', '择业期', '往届', '应届', '招聘'])

page = ChromiumPage(port)
tab = page.new_tab(url)
time.sleep(9)
print('URL :', (tab.url or '')[:110])
print('标题:', (tab.title or '')[:90])
body = tab.ele('tag:body').text or ''
print('正文长度:', len(body))
print('--- 命中片段 ---')
seen = set()
n = 0
for line in re.split(r'[\n\r]+', body):
    s = line.strip()
    if not s or len(s) < 4 or s in seen:
        continue
    if any(k in s for k in kws):
        seen.add(s)
        print(' ·', s[:180])
        n += 1
        if n >= 40:
            break
if n == 0:
    print('  (无命中) 正文前 400 字：')
    print(body[:400])

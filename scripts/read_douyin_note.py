#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读抖音图文笔记（note）的正文与图片。

背景：yt-dlp 不支持 douyin.com/note/<id>（图文笔记，非视频）；抖音链接短链也常
解析成 note。所以走浏览器渲染读取，取正文文本 + 图片 URL（图片再交 vision 识别）。
用法： python3 read_douyin_note.py <url> [port]
"""
import json
import sys
import time

from DrissionPage import ChromiumPage

url = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 9224

page = ChromiumPage(port)
tab = page.new_tab(url)
time.sleep(12)

print('URL  :', (tab.url or '')[:110])
print('标题 :', (tab.title or '')[:100])

body = tab.ele('tag:body').text or ''
print('正文长度:', len(body))
print('--- 正文 ---')
# 抖音页首屏通常有大量导航噪音，截取包含关键词的区间
print(body[:2500])

print()
print('--- 图片 URL ---')
imgs = tab.run_js(r"""return (() => {
  const out = [];
  document.querySelectorAll('img').forEach(i => {
    const s = i.src || i.getAttribute('data-src') || '';
    if (/douyinpic|aweme|tos-cn/.test(s) && !/avatar/.test(s)) out.push(s);
  });
  return [...new Set(out)].slice(0, 20).join('\n');
})()""")
print(imgs or '(无)')
open('/tmp/dy_note.json', 'w', encoding='utf-8').write(
    json.dumps({'url': tab.url, 'title': tab.title, 'body': body,
                'imgs': (imgs or '').split('\n')}, ensure_ascii=False, indent=1))
print('\n已存 /tmp/dy_note.json')

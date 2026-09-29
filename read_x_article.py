#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：用 job-hunter Chrome 打开 X 文章，把正文抓出来（不登录也试）。"""
import sys, time, re
sys.path.insert(0, ".")
from chrome_lock import acquire
from DrissionPage import ChromiumPage, ChromiumOptions

URL = "https://x.com/i/article/2100423942741831680"

def main():
    if not acquire("x-article-read", wait_seconds=600, max_minutes=6):
        print("Chrome 被投递任务占用，稍后再试")
        return 1
    co = ChromiumOptions().set_local_port(9223)
    page = ChromiumPage(co)
    tab = page.new_tab(URL)
    txt = ""
    try:
        for i in range(20):
            time.sleep(2)
            txt = tab.ele('tag:body', timeout=5).text or ""
            if len(txt) > 400:
                break
        print("URL:", tab.url[:120])
        print("标题:", tab.title)
        print("正文字符数:", len(txt))
        print("=" * 60)
        print(txt[:6000])
        # 顺带看有没有登录墙
        for h in ["登录", "Sign in", "Log in", "JavaScript"]:
            if h in txt[:600]:
                print(f"\n⚠️ 疑似登录墙/JS墙关键词: {h}")
        return 0
    finally:
        try:
            tab.close()
        except Exception:
            pass

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 Boss 聊天页未读消息（只读，不发送）"""
import sys, json, time
sys.path.insert(0, '/usr/lib/python3')
from DrissionPage import ChromiumPage, ChromiumOptions

def main():
    co = ChromiumOptions().set_local_port(9223)
    try:
        page = ChromiumPage(co)
    except Exception as e:
        print("CHROME_ERR:", e); return 1

    # 找 zhipin chat tab
    target = None
    for tid in page.tab_ids:
        try:
            t = page.get_tab(tid)
            if 'zhipin.com/web/geek/chat' in (t.url or ''):
                target = t; break
        except Exception:
            continue
    if target is None:
        print("NO_CHAT_TAB"); return 1
    target.set.activate()
    time.sleep(3)

    print("URL:", target.url)
    print("TITLE:", target.title)

    # 登录态判断
    body = target.ele('tag:body', timeout=5)
    txt = body.text[:600] if body else ''
    if '验证码登录' in txt or '扫码登录' in txt or '登录' in txt and 'geek/chat' not in target.url:
        print("LOGIN_REQUIRED")
    print("--- 页面文本前400字 ---")
    print(txt[:400])
    print("---")

    # 扫会话列表
    items = target.eles('css:.user-list li, css:.geek-chat-list li, css:[class*=user-item]')
    print("会话数:", len(items))
    for it in items[:25]:
        try:
            t = it.text.replace('\n', ' | ')[:150]
            if t.strip():
                print("-", t)
        except Exception:
            pass
    return 0

if __name__ == '__main__':
    sys.exit(main())

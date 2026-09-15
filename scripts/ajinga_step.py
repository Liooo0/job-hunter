#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""菁客(AJINGA)申请补全 —— 步骤驱动。

用法:
    ajinga_step.py upload <pdf路径>     # 在已打开的「上传简历」步骤上传并前进
    ajinga_step.py state                # 打印当前步骤状态 + 交互元素
    ajinga_step.py click "<按钮文字>"     # 点按钮
    ajinga_step.py shot <保存路径>        # 截图（供 vision 核验）
"""
import sys
import time

from DrissionPage import ChromiumPage

PORT = 9224


def get_tab(page):
    for t in page.get_tabs():
        if t.url and 'ajinga' in (t.url or ''):
            return t
    raise SystemExit('❌ 没找到 ajinga 标签页')


def do_state(tab):
    print('URL  :', tab.url[:140])
    print('标题 :', tab.title[:90])
    print('--- 正文 ---')
    print(tab.ele('tag:body').text[:1400])
    print('--- 按钮 ---')
    for b in tab.eles('tag:button')[:20]:
        try:
            print(f"  {b.text.strip()[:30]!r:34} class={(b.attr('class') or '')[:70]}")
        except Exception:
            pass
    print('--- 输入框 ---')
    for i in tab.eles('tag:input')[:20]:
        try:
            print(f"  type={i.attr('type')} placeholder={str(i.attr('placeholder'))[:40]!r} "
                  f"class={(i.attr('class') or '')[:50]} value={str(i.attr('value'))[:30]!r}")
        except Exception:
            pass


def do_upload(tab, pdf):
    import os
    pdf = os.path.abspath(os.path.expanduser(pdf))
    if not os.path.exists(pdf):
        raise SystemExit(f'❌ 文件不存在: {pdf}')
    print('上传文件:', pdf, f'({os.path.getsize(pdf)} 字节)')
    # 用 CDP DOM.setFileInputFiles（对 Element UI 隐藏 input 唯一可靠的办法）
    doc = tab.run_cdp('DOM.getDocument', depth=-1)
    root = doc['root']['nodeId']
    q = tab.run_cdp('DOM.querySelector', nodeId=root, selector='input[type=file]')
    node = q.get('nodeId')
    print('file input nodeId:', node)
    if not node:
        raise SystemExit('❌ 找不到 input[type=file]')
    tab.run_cdp('DOM.setFileInputFiles', files=[pdf], nodeId=node)
    time.sleep(3)
    # 上传后按钮通常变「开始上传」/「下一步」
    for label in ('开始上传', '下一步', '上传', '确定'):
        try:
            b = tab.ele(f'xpath://button[contains(., "{label}")]', timeout=1.5)
        except Exception:
            b = None
        if b:
            print(f'点击按钮: {label}')
            b.click()
            time.sleep(6)
            break
    else:
        print('⚠️ 未找到推进按钮')
    do_state(tab)


def do_click(tab, text):
    b = tab.ele(f'xpath://button[contains(., "{text}")]', timeout=3)
    if not b:
        raise SystemExit(f'❌ 没有按钮 {text}')
    b.click()
    time.sleep(5)
    do_state(tab)


def do_shot(tab, path):
    import os
    path = os.path.abspath(os.path.expanduser(path))
    tab.get_screenshot(path=path)
    print('截图:', path, os.path.getsize(path), '字节')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'state'
    page = ChromiumPage(PORT)
    tab = get_tab(page)
    if cmd == 'state':
        do_state(tab)
    elif cmd == 'upload':
        do_upload(tab, sys.argv[2])
    elif cmd == 'click':
        do_click(tab, sys.argv[2])
    elif cmd == 'shot':
        do_shot(tab, sys.argv[2] if len(sys.argv) > 2 else '/tmp/ajinga.png')
        do_state(tab)
    else:
        print(__doc__)

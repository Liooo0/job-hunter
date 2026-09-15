#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""菁客表单填充：性别(el-select) + 出生日期(el-date-picker)。

Element UI 的坑（2026-09-12 踩过，别再用别的方法）：
- el-select：直接改 input.value 对 Vue 无效 → JS click 开下拉 → 点 .el-select-dropdown__item
- el-date-picker：改 value 无效 → focus 输入框 → CDP Input.insertText → Enter
"""
import sys
import time

from DrissionPage import ChromiumPage

PORT = 9224
GENDER = '男'
BIRTH = '2001-12-14'

page = ChromiumPage(PORT)
tabs = [x for x in page.get_tabs() if 'ajinga' in (x.url or '')]
if not tabs:
    raise SystemExit('❌ 没有 ajinga 标签')
tab = tabs[0]

# ── 性别 ──
sel_js = r"""return (() => {
  const items = document.querySelectorAll('.el-form-item');
  for (const it of items) {
    const lab = it.querySelector('.el-form-item__label');
    if (lab && lab.innerText.trim() === '性别') {
      const inp = it.querySelector('.el-select input, input');
      if (inp) { inp.click(); return 'clicked'; }
    }
  }
  return 'not-found';
})()"""
print('性别下拉:', tab.run_js(sel_js))
time.sleep(1.2)

pick_js = r"""return (() => {
  const opts = document.querySelectorAll('.el-select-dropdown:not([style*="display: none"]) .el-select-dropdown__item, .el-select-dropdown__item');
  const seen = [];
  for (const o of opts) {
    const t = (o.innerText || '').trim();
    seen.push(t);
    if (t === '%s') { o.click(); return 'picked:' + t; }
  }
  return 'options=' + seen.join(',');
})()""" % GENDER
print('选性别:', tab.run_js(pick_js))
time.sleep(1)

# ── 出生日期 ──
focus_js = r"""return (() => {
  const items = document.querySelectorAll('.el-form-item');
  for (const it of items) {
    const lab = it.querySelector('.el-form-item__label');
    if (lab && lab.innerText.trim() === '出生日期') {
      const inp = it.querySelector('input');
      if (!inp) return 'no-input';
      inp.focus();
      inp.click();
      return 'focused';
    }
  }
  return 'not-found';
})()"""
print('出生日期聚焦:', tab.run_js(focus_js))
time.sleep(0.8)

# 清空后输入（insertText 是 CDP 原生输入，Vue 能收到）
tab.run_cdp('Input.dispatchKeyEvent', type='keyDown', windowsVirtualKeyCode=65, modifiers=4)  # Cmd+A
tab.run_cdp('Input.dispatchKeyEvent', type='keyUp', windowsVirtualKeyCode=65, modifiers=4)
tab.run_cdp('Input.insertText', text=BIRTH)
time.sleep(0.6)
tab.run_cdp('Input.dispatchKeyEvent', type='keyDown', windowsVirtualKeyCode=13)
tab.run_cdp('Input.dispatchKeyEvent', type='keyUp', windowsVirtualKeyCode=13)
time.sleep(1.5)
print('已输入出生日期:', BIRTH)

# ── 复核 ──
check_js = r"""return (() => {
  const out = [];
  document.querySelectorAll('.el-form-item').forEach((it, i) => {
    const labEl = it.querySelector('.el-form-item__label');
    const lab = labEl ? (labEl.innerText || '').trim() : '';
    const inp = it.querySelector('input');
    out.push(lab + ' = ' + (inp ? String(inp.value || '') : ''));
  });
  return out.join('\n');
})()"""
print('--- 复核 ---')
print(tab.run_js(check_js))

if '--next' in sys.argv:
    tab.ele('xpath://button[contains(., "下一步")]', timeout=3).click()
    time.sleep(6)
    print('--- 点击下一步后 ---')
    print(tab.url[:120])
    print(tab.ele('tag:body').text[:900])

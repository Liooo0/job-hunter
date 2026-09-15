#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""九联「应聘岗位」级联选择：先点分类（如 软件开发岗），再点岗位（如 AI应用开发工程师）。

2026-09-15 实测：该字段是 el-input 外观的**级联/树形选择**，不是自由输入也不是普通联想——
输入文字后弹出的是分类列表（无匹配数据|管培生|软件开发岗|测试和技术支持岗|…），
必须沿「分类 → 岗位」两级点选才被 Vue 认可。
"""
import sys
import time

from DrissionPage import ChromiumPage

p = ChromiumPage(9224)
tab = next(x for x in p.get_tabs() if 'unionman' in (x.url or ''))

VIS_CLICK = """return (() => {
  const vis = (e) => { try { const r = e.getBoundingClientRect(); return r.height > 0 && r.width > 0; } catch (x) { return false; } };
  const nodes = [...document.querySelectorAll('li, .el-select-dropdown__item, .el-cascader-node, span, div')]
                  .filter(vis).filter(e => (e.innerText || '').trim() === '%s');
  if (!nodes.length) {
    const all = [...document.querySelectorAll('li, .el-cascader-node, .el-select-dropdown__item')]
                  .filter(vis).map(e => (e.innerText || '').trim()).filter(Boolean);
    return 'miss; visible=' + all.slice(0, 14).join('|');
  }
  // 取最深（最具体）的那个节点，避免点到外层容器
  const target = nodes.sort((a, b) => b.compareDocumentPosition(a) & 2 ? 1 : -1)[0];
  target.click();
  return 'clicked:' + (target.innerText || '').trim();
})()"""

FOCUS = """return (() => {
  for (const it of document.querySelectorAll('.el-form-item')) {
    const l = it.querySelector('.el-form-item__label');
    if (l && l.innerText.indexOf('%s') >= 0) {
      const i = it.querySelector('input');
      if (!i) return 'no-input';
      i.focus(); i.click(); return 'focused';
    }
  }
  return 'not-found';
})()"""

VAL = """return (() => {
  for (const it of document.querySelectorAll('.el-form-item')) {
    const l = it.querySelector('.el-form-item__label');
    if (l && l.innerText.indexOf('%s') >= 0) {
      const i = it.querySelector('input');
      return i ? String(i.value || '') : '?';
    }
  }
  return '?';
})()"""

field, group, pos = sys.argv[1], sys.argv[2], sys.argv[3]
print('focus:', tab.run_js(FOCUS % field))
time.sleep(0.8)
# 清掉可能已输入的文字
tab.run_cdp('Input.dispatchKeyEvent', type='keyDown', windowsVirtualKeyCode=65, modifiers=4)
tab.run_cdp('Input.dispatchKeyEvent', type='keyUp', windowsVirtualKeyCode=65, modifiers=4)
tab.run_cdp('Input.dispatchKeyEvent', type='keyDown', windowsVirtualKeyCode=8)
tab.run_cdp('Input.dispatchKeyEvent', type='keyUp', windowsVirtualKeyCode=8)
time.sleep(0.8)
print('点分类:', tab.run_js(VIS_CLICK % group))
time.sleep(1.2)
print('点岗位:', tab.run_js(VIS_CLICK % pos))
time.sleep(1.0)
print('现值:', repr(tab.run_js(VAL % field)))

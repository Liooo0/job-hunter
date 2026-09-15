#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""九联科技 应聘信息补充表 —— 填「已确知」的星号项 + 列出仍缺项。

纪律：只填可核实的事实（性别=男 来自历史确认；学历=本科 来自学历事实）。
月份/民族/政治面貌/绩点/外语/所在地等只有用户知道 —— 一律不猜，列出来等他给。
"""
import json
import time

from DrissionPage import ChromiumPage

PORT = 9224
FILL = {'性别': '男', '学历': '本科'}
try:
    with open('/tmp/jl_port', encoding='utf-8') as _f:
        PORT = int((_f.read() or '9224').strip())
except Exception:
    pass

page = ChromiumPage(PORT)
tab = None
for t in page.get_tabs():
    if 'unionman' in (t.url or ''):
        tab = t
        break
if tab is None:
    raise SystemExit('❌ 没找到 unionman 标签页（表还开着吗）')


def form_state():
    js = r"""return (() => {
      const rows = [];
      document.querySelectorAll('.el-form-item').forEach((it) => {
        const l = it.querySelector('.el-form-item__label');
        const lab = l ? (l.innerText || '').trim() : '';
        const inp = it.querySelector('input, textarea');
        const v = inp ? String(inp.value || '') : '';
        const req = (it.className || '').indexOf('is-required') >= 0 ||
                    (l && (l.className || '').indexOf('is-required') >= 0);
        rows.push({lab: lab, val: v, req: !!req});
      });
      return JSON.stringify(rows);
    })()"""
    return json.loads(tab.run_js(js) or '[]')


def set_select(label, value):
    """Element UI el-select：点开下拉再点选项（直接改 value 对 Vue 无效）。"""
    click = r"""return (() => {
      const items = document.querySelectorAll('.el-form-item');
      for (const it of items) {
        const l = it.querySelector('.el-form-item__label');
        if (l && l.innerText.trim() === '%s') {
          const inp = it.querySelector('.el-select input, input');
          if (inp) { inp.click(); return 'opened'; }
        }
      }
      return 'label-not-found';
    })()""" % label
    st = tab.run_js(click)
    time.sleep(1.2)
    pick = r"""return (() => {
      const opts = [...document.querySelectorAll('.el-select-dropdown:not([style*="display: none"]) .el-select-dropdown__item, .el-select-dropdown__item')];
      const labels = opts.map(o => (o.innerText || '').trim());
      for (const o of opts) {
        if ((o.innerText || '').trim() === '%s') { o.click(); return 'picked:' + o.innerText.trim(); }
      }
      return 'options=' + labels.slice(0, 25).join('|');
    })()""" % value
    return st + ' / ' + tab.run_js(pick)


print('=== 填写前状态 ===')
before = form_state()
print(f'  表单项 {len(before)} 个')
missing_before = [r['lab'] for r in before if r['req'] and not r['val'].strip()]
print('  必填但为空:', len(missing_before))

print('\n=== 逐项填写（只填确知事实）===')
for lab, val in FILL.items():
    print(f'  {lab} = {val} ->', set_select(lab, val))
    time.sleep(0.8)

print('\n=== 填写后状态 ===')
after = form_state()
filled_ok = []
still = []
for r in after:
    if r['val'].strip():
        filled_ok.append(f"{r['lab']}={r['val'][:26]}")
    elif r['req']:
        still.append(r['lab'])
print('  已填:', ' | '.join(filled_ok))
print()
print(f'  ⚠️ 仍是必填空项（{len(still)} 项，只有你知道）:')
for s in still:
    print('     -', s)

# 第一志愿岗位的可选项（用户说不清楚投的是哪个岗）
opts = tab.run_js(r"""return (() => {
  const items = document.querySelectorAll('.el-form-item');
  for (const it of items) {
    const l = it.querySelector('.el-form-item__label');
    if (l && l.innerText.indexOf('第一志愿') >= 0) {
      const inp = it.querySelector('.el-select input, input');
      if (inp) { inp.click(); return 'opened'; }
    }
  }
  return 'not-found';
})()""")
time.sleep(1.2)
print('\n=== 「应聘岗位(第一志愿)」可选项 ===')
print(tab.run_js(r"""return (() => {
  const opts = [...document.querySelectorAll('.el-select-dropdown:not([style="display: none"]) .el-select-dropdown__item')];
  return opts.length ? opts.map(o => (o.innerText || '').trim()).join('\n') : '(下拉未展开或列表为空)';
})()"""))
# 关掉下拉，避免影响后续
tab.run_js("return document.body.click(), 'closed';")

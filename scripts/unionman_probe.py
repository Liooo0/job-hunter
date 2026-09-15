#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测九联表所有 el-select 的可选项 + 填「可核实」的项。

可核实 = 有据可查的事实（不是猜）：
- 性别=男（用户档案）
- 外语能力=英语四级（用户档案记录 CET-4）
其余（民族/政治面貌/所在地/绩点/是否类）只有用户知道 → 不填，列给他。
"""
import json
import time

from DrissionPage import ChromiumPage

p = ChromiumPage(9224)
tab = next(x for x in p.get_tabs() if 'unionman' in (x.url or ''))

OPEN = """return (() => {
  for (const it of document.querySelectorAll('.el-form-item')) {
    const l = it.querySelector('.el-form-item__label');
    if (l && l.innerText.indexOf('%s') >= 0) {
      const inp = it.querySelector('.el-select input, input');
      if (!inp) return 'no-input';
      inp.focus(); inp.click(); return 'opened';
    }
  }
  return 'not-found';
})()"""

VIS = """return (() => {
  const vis = (e) => { try { return e.getBoundingClientRect().height > 0; } catch (x) { return false; } };
  const o = [...document.querySelectorAll('.el-select-dropdown__item')].filter(vis)
              .map(e => (e.innerText || '').trim());
  return o.length ? o.join('|') : '';
})()"""

CLOSE = "return (() => { document.body.click(); return 1; })()"

labels = ['性别', '民族', '学历', '外语能力', '家庭所在地', '户籍所在地', '政治面貌',
          '是否已有心仪offer', '是否有考研', '是否接受惠州工作',
          '是否有考公务员/事业单位', '招聘信息来源', '第一志愿', '第二志愿']

opts = {}
for lab in labels:
    st = tab.run_js(OPEN % lab)
    time.sleep(1.1)
    o = tab.run_js(VIS) or ''
    opts[lab] = o
    print(f'【{lab}】{st} -> {o[:150] if o else "(空/一次拉不到)"}')
    tab.run_js(CLOSE)
    time.sleep(0.7)

with open('/tmp/unionman_opts.json', 'w', encoding='utf-8') as f:
    json.dump(opts, f, ensure_ascii=False, indent=1)
print('\n选项已存 /tmp/unionman_opts.json')

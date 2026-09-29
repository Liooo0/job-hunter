#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""51job 在线简历「求职意向」编辑（CDP 直连，Element Plus）

用法：
  python3 scripts/apply_51job_intent.py            # 只读：打开表单并 dump 当前状态
  python3 scripts/apply_51job_intent.py --apply    # 实际修改并保存

设计依据（references/ats-form-automation.md）：
  · Element Plus 的 input.value 恒为空 → 一律读渲染文本
  · 下拉容器按几何可见性判，别按 style 属性
  · 每改一个字段立刻复核；保存后刷新再看（刷新仍在 = 真存到服务端）
  · 只读模式不点任何「保存」
"""
import json
import sys
import time

sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab  # noqa: E402
import websocket  # noqa: E402

APPLY = "--apply" in sys.argv
URL = "https://www.51job.com/resume/center"


class Cdp:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.i = 0

    def call(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                return msg.get("result", {})

    def js(self, expr, timeout=25):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True)
        return r.get("result", {}).get("value")

    def mouse(self, x, y, kind="move"):
        t = {"move": "mouseMoved", "down": "mousePressed", "up": "mouseReleased"}[kind]
        self.call("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left",
                  clickCount=1 if kind != "move" else 0)

    def click_at(self, x, y):
        self.mouse(x, y, "move")
        time.sleep(0.3)
        self.mouse(x, y, "down")
        time.sleep(0.1)
        self.mouse(x, y, "up")


# ── JS 片段（CDP Runtime.evaluate：裸表达式，不能带 return）──
FIND_BLOCK = r"""
(() => {
  const e = document.querySelector('div.careerObjectiveSet');
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), txt: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 120)});
})()
"""

FIND_EDIT = r"""
(() => {
  for (const sel of ['div.content_editclick', 'div.content_title_edit', 'div.careerObjective_content_hover']) {
    const e = document.querySelector(sel);
    if (!e) continue;
    const r = e.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      return JSON.stringify({sel: sel, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), txt: (e.innerText || '').trim()});
    }
  }
  return 'NONE_VISIBLE';
})()
"""

SNAPSHOT = r"""
(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.height > 0 && r.width > 0; };
  const out = {selects: [], radios: [], radiosActive: []};
  [...document.querySelectorAll('.el-select')].forEach((s, i) => {
    if (!vis(s)) return;
    const r = s.getBoundingClientRect();
    out.selects.push({i: i, txt: (s.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 36),
                      x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)});
  });
  [...document.querySelectorAll('.el-radio-button')].forEach(r0 => {
    if (!vis(r0)) return;
    out.radios.push((r0.innerText || '').trim().slice(0, 8));
    if (/is-active|is-checked/.test((r0.className || '').toString())) {
      out.radiosActive.push((r0.innerText || '').trim().slice(0, 8));
    }
  });
  const all = (document.body.innerText || '').replace(/\s+/g, ' ');
  const i = all.indexOf('求职意向');
  out.seg = i >= 0 ? all.slice(i, i + 300) : '';
  out.modal = !!document.querySelector('.el-dialog, .el-drawer, .el-overlay');
  return JSON.stringify(out);
})()
"""


def main():
    tab = open_tab(URL)
    tid, ws = tab.get("id"), tab.get("webSocketDebuggerUrl")
    try:
        c = Cdp(ws)
        c.call("Runtime.enable")
        for _ in range(10):
            time.sleep(3)
            if c.js(FIND_BLOCK):
                break
        b = json.loads(c.js(FIND_BLOCK) or "null" or "null")
        if not b:
            print("✗ 找不到求职意向区块")
            return
        print("① 求职意向区块:", b["txt"])
        c.mouse(b["x"], b["y"], "move")      # 悬停露出编辑控件
        time.sleep(2)
        ed = c.js(FIND_EDIT)
        print("② 编辑控件:", ed)
        if ed == "NONE_VISIBLE" or not ed:
            print("✗ 悬停后仍看不到编辑控件")
            return
        e = json.loads(ed)
        c.click_at(e["x"], e["y"])
        time.sleep(6)

        snap = json.loads(c.js(SNAPSHOT) or "{}")
        print("③ 表单是否出现:", snap.get("modal"), "| 可见下拉数:", len(snap.get("selects") or []))
        for s in (snap.get("selects") or []):
            print("    #%s 值=%-24s @%s,%s" % (s["i"], s["txt"], s["x"], s["y"]))
        print("    单选:", snap.get("radios"), "| 选中:", snap.get("radiosActive"))
        print("    区域文本:", (snap.get("seg") or "")[:200])
        if not APPLY:
            print("\n（只读模式结束；加 --apply 才会真正修改并保存）")
    finally:
        close_tab(tid)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读实验：让 Boss 聊天列表真正被搜索框过滤（用 CDP 真实按键，而非 JS 赋值）。

结论用于修 send_one 的会话定位：Boss 聊天列表是虚拟滚动，
直接 JS 改 input.value 不会触发 React 过滤；必须派发真实按键事件。
"""
import json
import sys
import time

sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab  # noqa: E402
import websocket  # noqa: E402

FIND_BOX = r"""
(() => {
  const b = document.querySelector('input.boss-search-input');
  if (!b) return 'NO_BOX';
  const r = b.getBoundingClientRect();
  b.focus();
  return JSON.stringify({x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)});
})()
"""

COUNT = r"""
(() => {
  const items = [...document.querySelectorAll('li')]
    .map(l => (l.innerText||'').replace(/\s+/g,' ').trim())
    .filter(t => t.length > 12 && /女士|先生|HR|招聘|职位/.test(t));
  return JSON.stringify({n: items.length, sample: items.slice(0, 8)});
})()
"""


class Cdp:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=25, suppress_origin=True)
        self.i = 0

    def call(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                return msg.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def type_text(self, text):
        for ch in text:
            self.call("Input.insertText", text=ch)
            time.sleep(0.12)

    def click(self, x, y):
        for t in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
            time.sleep(0.1)

    def clear_box(self):
        self.call("Input.dispatchKeyEvent", type="keyDown", windowsVirtualKeyCode=65, modifiers=4)  # Ctrl/Cmd+A
        self.call("Input.dispatchKeyEvent", type="keyUp", windowsVirtualKeyCode=65, modifiers=4)
        time.sleep(0.2)
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, windowsVirtualKeyCode=8)  # Backspace
        time.sleep(0.3)


tab = open_tab("https://www.zhipin.com/web/geek/chat")
tid, ws_url = tab.get("id"), tab.get("webSocketDebuggerUrl")
try:
    time.sleep(8)
    c = Cdp(ws_url)
    c.call("Runtime.enable")
    base = json.loads(c.js(COUNT) or "{}")
    print(f"初始会话条目: {base.get('n')}")
    for s in (base.get("sample") or [])[:3]:
        print(f"   {s[:64]}")

    pos = c.js(FIND_BOX)
    print(f"\n搜索框位置: {pos}")
    if pos and pos != "NO_BOX":
        p = json.loads(pos)
        c.click(p["x"], p["y"])
        time.sleep(0.6)
        c.type_text("慧博")
        time.sleep(3)
        after = json.loads(c.js(COUNT) or "{}")
        print(f"\n输入「慧博」后条目: {after.get('n')}（减少=过滤生效）")
        for s in (after.get("sample") or [])[:5]:
            print(f"   {s[:64]}")
finally:
    close_tab(tid)

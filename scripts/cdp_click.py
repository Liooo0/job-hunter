#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 cdp51 补一个「真实鼠标点击」——用 CDP Input.dispatchMouseEvent，
这样才算用户手势，window.open 弹窗不会被拦。

用法（被 import）：
    from cdp_click import real_click_by_text
    real_click_by_text(ws, "立即沟通", index=0)
"""
import json
import time

import websocket  # DrissionPage 依赖，本机有


def _ws(ws_url_or_conn, timeout=25):
    """接受 ws 连接或 URL 字符串，统一返回一个连接。"""
    if hasattr(ws_url_or_conn, "send"):
        return ws_url_or_conn
    return websocket.create_connection(str(ws_url_or_conn), timeout=timeout,
                                        suppress_origin=True)


def _send(ws, method, params=None, msg_id=1):
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    deadline = time.time() + 15
    while time.time() < deadline:
        msg = json.loads(ws.recv())
        if msg.get("id") == msg_id:
            return msg.get("result", {})
    return {}


def eval_js(ws, expr, msg_id=1):
    ws = _ws(ws)
    r = _send(ws, "Runtime.evaluate",
              {"expression": expr, "returnByValue": True, "awaitPromise": True}, msg_id)
    return r.get("result", {}).get("value")


def real_click_at(ws, x, y, msg_id_base=100):
    """在视口坐标 (x, y) 上做一次真实鼠标点击。"""
    ws = _ws(ws)
    for i, etype in enumerate(("mouseMoved", "mousePressed", "mouseReleased")):
        params = {"type": etype, "x": x, "y": y, "button": "left",
                  "clickCount": 1, "buttons": 1 if etype == "mousePressed" else 0}
        _send(ws, "Input.dispatchMouseEvent", params, msg_id_base + i)
        time.sleep(0.05)
    return f"clicked({x},{y})"


def real_click_by_text(ws, text, index=0, tag_filter="a,button,span,div"):
    """找到文字完全等于 text 的可见元素，用真实鼠标点它。返回描述字符串。"""
    expr = r"""
    (() => {
      const want = %s;
      const idx = %d;
      const els = [...document.querySelectorAll(%s)].filter(el =>
        (el.textContent || '').trim() === want);
      const vis = els.filter(el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.top >= 0 && r.top < window.innerHeight;
      });
      const el = vis[idx];
      if (!el) return JSON.stringify({ok: false, total: vis.length});
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({ok: true, total: vis.length,
        x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
        txt: (el.textContent || '').trim()});
    })()
    """ % (json.dumps(text), index, json.dumps(tag_filter))
    d = json.loads(eval_js(ws, expr, 1) or "{}")
    if not d.get("ok"):
        return f"not_found({text}, 可见匹配 {d.get('total')} 个)"
    time.sleep(0.4)
    return f"{d['txt']} @ real_click" + " " + real_click_at(ws, d["x"], d["y"])


def cdp(ws, method, params=None, msg_id=900):
    return _send(ws, method, params, msg_id)


if __name__ == "__main__":
    print(__doc__)

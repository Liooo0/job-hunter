#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用原生 CDP HTTP + websocket 读 51job 页面（绕开 DrissionPage 的 get_tab 超时）
只读。用法: python3 scripts/cdp51.py <url> [<url> ...]
"""
import json
import re
import sys
import time
import urllib.request

PORT = 9223


def http(path, method="GET", timeout=15):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def open_tab(url):
    try:
        return json.loads(http(f"/json/new?{urllib.parse.quote(url, safe='')}", method="PUT"))
    except Exception:
        # 老版本 Chrome 用 GET
        raw = http(f"/json/new?{urllib.parse.quote(url, safe='')}")
        return json.loads(raw)


def close_tab(tid):
    try:
        http(f"/json/close/{tid}")
    except Exception:
        pass


def read_via_ws(ws_url, expr="document.body.innerText", timeout=20):
    """通过 websocket 执行 JS 取结果（标准库无 ws，用 websocket-client）。"""
    import websocket  # DrissionPage 的依赖，本机有
    # Chrome 会对带 Origin 头的 CDP 握手回 403；suppress_origin 绕过
    ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True,
                                       "awaitPromise": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 2:
                res = msg.get("result", {}).get("result", {})
                return res.get("value")
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


import urllib.parse  # noqa: E402

for url in sys.argv[1:]:
    print(f"\n═══ {url} ═══")
    tab = None
    try:
        tab = open_tab(url)
        tid, ws_url = tab.get("id"), tab.get("webSocketDebuggerUrl")
        if not ws_url:
            print("  ✗ 没拿到 webSocketDebuggerUrl")
            continue
        # 等页面真的渲染出来：轮询 readyState + body 有字（最多 ~25s）
        body = ""
        for _ in range(12):
            time.sleep(2)
            href = read_via_ws(ws_url, "location.href") or ""
            body = read_via_ws(ws_url, "document.body.innerText") or ""
            if body.strip() or ("51job" in str(href) and _ >= 3):
                break
        print(f"  实际地址: {str(read_via_ws(ws_url, 'location.href'))[:100]}")
        title = read_via_ws(ws_url, "document.title")
        print(f"  标题: {str(title)[:70]}")
        txt = " ".join(body.split())
        print(f"  内容({len(txt)}字): {txt[:600]}")
        # 消息类线索
        for kw in ("未读", "消息", "沟通", "HR", "简历", "投递"):
            if kw in txt:
                for m in re.finditer(kw, txt):
                    s = txt[max(0, m.start() - 40):m.start() + 60]
                    print(f"    🔎[{kw}] …{s}…")
                    break
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
    finally:
        if tab and tab.get("id"):
            close_tab(tab["id"])

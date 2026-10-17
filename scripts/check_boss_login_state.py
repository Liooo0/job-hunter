#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读复核指定 zhipin tab（按 targetId）的登录态 —— Boss 投递前的前置检查。

背景（2026-09-27 事故）：Boss 登录态过期后，boss_apply 对游客态空转 16 combo
0 投递、被误以为在跑。后续轮次必须先复核登录再决定是否跑 boss_apply。

用法：
  1) env PYTHONPATH="" /usr/bin/python3 ~/.hermes/scripts/bg_new_tab.py 9223 "https://www.zhipin.com/web/geek/jobs"
     （必须用 bg_new_tab.py，绝不用 PUT /json/new）
  2) env PYTHONPATH="" python3 scripts/check_boss_login_state.py <targetId>

本脚本只读该 tab + 关闭该 tab；不导航、不触碰 /web/user 登录页（用户扫码面）。

判定：
  - cards > 0                      → LOGGED_IN（exit 0）
  - 含「登录/注册」/「没有更多职位」/ 被跳 /web/user → GUEST（exit 2）
  - 其它                            → UNKNOWN（exit 1）
"""
import json
import sys
import time
import urllib.request

import websocket

PORT = 9223


def http_json(url):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(urllib.request.Request(url), timeout=8) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def main():
    if len(sys.argv) < 2:
        print("用法: check_boss_login_state.py <targetId>")
        return 1
    tid = sys.argv[1]
    tabs = http_json(f"http://127.0.0.1:{PORT}/json/list")
    t = next((x for x in tabs if x.get("id") == tid), None)
    if not t:
        print(f"❌ 找不到 tab {tid}（可能已关闭）")
        return 1
    ws_url = t.get("webSocketDebuggerUrl")
    if not ws_url:
        print(f"❌ tab {tid} 无调试端点（type={t.get('type')}）")
        return 1
    ws = websocket.create_connection(
        ws_url, timeout=30, origin="devtools://devtools",
        http_proxy_host=None, https_proxy_host=None, suppress_origin=True)
    mid = 0

    def send(method, params=None):
        nonlocal mid
        mid += 1
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid:
                return m

    try:
        send("Runtime.enable")
    except Exception:
        pass
    # 等待页面 readyState=complete（最多 ~25s）
    for _ in range(25):
        try:
            r = send("Runtime.evaluate", {"expression": "document.readyState",
                                          "returnByValue": True})
            if (r.get("result") or {}).get("result", {}).get("value") == "complete":
                break
        except Exception:
            pass
        time.sleep(1)
    time.sleep(3)  # 给前端渲染留时间

    js = """(() => {
      const body = (document.body && document.body.innerText) || '';
      return JSON.stringify({
        url: location.href,
        title: document.title,
        cards: document.querySelectorAll('.job-card-wrapper, li.job-card-box, [class*=job-card]').length,
        guest: body.includes('登录/注册'),
        empty: body.includes('没有更多职位'),
        head: body.slice(0, 200)
      });
    })()"""
    info = {}
    try:
        r = send("Runtime.evaluate", {"expression": js, "returnByValue": True})
        val = (r.get("result") or {}).get("result", {}).get("value")
        if val:
            info = json.loads(val)
    except Exception as e:
        print("读取失败:", type(e).__name__, e)
    ws.close()

    print(json.dumps(info, ensure_ascii=False, indent=1))

    # 关闭复核 tab（HTTP /json/close，不带窗口动作）
    try:
        http_json(f"http://127.0.0.1:{PORT}/json/close/" + tid)
        print("已关闭复核 tab")
    except Exception as e:
        print("关 tab 失败:", e)

    cards = info.get("cards")
    url = info.get("url") or ""
    if isinstance(cards, int) and cards > 0:
        print("VERDICT: LOGGED_IN")
        return 0
    if info.get("guest") or info.get("empty") or "/web/user" in url:
        print("VERDICT: GUEST")
        return 2
    print("VERDICT: UNKNOWN")
    return 1


if __name__ == "__main__":
    sys.exit(main())

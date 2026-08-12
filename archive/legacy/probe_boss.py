#!/usr/bin/env python3
"""Probe BOSS zhipin session health via Chrome 9222."""
import sys, time, json, urllib.request
from DrissionPage import ChromiumPage, ChromiumOptions, errors


def connect(port=9222, attempts=3):
    """Connect; if no tab exists, create one via CDP HTTP and retry."""
    for i in range(attempts):
        try:
            page = ChromiumPage(addr_or_opts=ChromiumOptions().set_local_port(port))
            tabs = page.get_tabs()
            if tabs:
                return page, tabs[0]
            page.new_tab("about:blank")
            time.sleep(2)
            return page, page.get_tabs()[0]
        except (errors.BrowserConnectError, Exception) as e:
            print(f"  connect attempt {i+1} failed: {type(e).__name__}")
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/json/new?https://www.zhipin.com/", method="PUT")
                urllib.request.urlopen(req, timeout=5)
                print("  restored a tab via CDP")
            except Exception as e2:
                print(f"  tab restore failed: {e2}")
            time.sleep(3)
    raise RuntimeError("cannot connect to chrome")


page, tab = connect()

def probe(url, wait=8):
    try:
        tab.get(url)
    except Exception as e:
        print(f"  nav error: {e}")
    time.sleep(wait)
    try:
        body = (tab.ele("body") or tab).text[:300].replace("\n", " | ")
    except Exception as e:
        body = f"(body err {e})"
    print(f"FINAL_URL: {tab.url[:160]}")
    print(f"BODY     : {body}")
    print("-" * 70)
    return tab.url

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "home"
    if mode == "home":
        probe("https://www.zhipin.com/")
    elif mode == "search":
        probe("https://www.zhipin.com/web/geek/job?query=AI%E5%AE%9E%E6%96%BD&city=101280600&degree=203,202&experience=101,108,102,103")
    elif mode == "loop":
        # probe repeatedly until session recovers or timeout
        import datetime
        deadline = time.time() + int(sys.argv[2]) if len(sys.argv) > 2 else 7200
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            print(f"[{datetime.datetime.now():%H:%M:%S}] probe #{attempt}")
            url = probe("https://www.zhipin.com/", wait=6)
            if "403" not in url and "passport" not in url:
                print("SESSION_OK")
                sys.exit(0)
            print(f"still blocked, sleeping 900s...")
            time.sleep(900)
        print("STILL_BLOCKED")
        sys.exit(1)

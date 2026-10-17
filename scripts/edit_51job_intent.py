#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打开 51job 简历中心的「求职意向」编辑表单并 dump（默认只读，不保存）

用法:
  python3 scripts/edit_51job_intent.py            # 只 dump 表单结构
  python3 scripts/edit_51job_intent.py --dump-only
"""
import sys
import time

sys.path.insert(0, ".")
from DrissionPage import ChromiumOptions, ChromiumPage

DUMP_ONLY = "--dump-only" in sys.argv or True

page = ChromiumPage(ChromiumOptions().set_local_port(9223))
tab = page.new_tab("https://www.51job.com/resume/center")
try:
    time.sleep(11)
    box = tab.ele("css:div.careerObjectiveSet", timeout=6)
    print("区块存在:", box is not None)
    box.hover()
    time.sleep(2)

    # 点「编辑」控件（悬停后才出现）
    clicked = False
    for sel in ("css:div.content_editclick", "css:div.content_title_edit", "css:div.careerObjective_content_hover"):
        e = box.ele(sel, timeout=3)
        if e is not None:
            try:
                e.click()
                clicked = True
                print(f"已点击编辑控件: {sel}")
                break
            except Exception as ex:
                print(f"  {sel} 点击失败: {ex}")
    if not clicked:
        raise SystemExit("找不到编辑控件")
    time.sleep(5)

    print("\n── 表单里的输入控件 ──")
    for e in tab.eles("css:input, textarea", timeout=5):
        try:
            r = e.rect
            w = getattr(r, "size", None)
            if not w:
                continue
            print(f"   {e.tag:8} ph={(e.attr('placeholder') or '')[:24]!r} "
                  f"val={(e.attr('value') or '')[:30]!r} cls={(e.attr('class') or '')[:30]}")
        except Exception:
            continue

    print("\n── 可点/下拉控件 ──")
    seen = set()
    for e in tab.eles("css:div,span,li,button", timeout=5):
        try:
            t = (e.text or "").strip()
            c = (e.attr("class") or "")
            if t and len(t) <= 12 and any(k in c for k in ("select", "dropdown", "btn", "save", "city", "salary", "job")):
                key = (t, c[:20])
                if key in seen:
                    continue
                seen.add(key)
                print(f"   {e.tag:6} cls={c[:38]:40} txt={t!r}")
                if len(seen) >= 22:
                    break
        except Exception:
            continue

    print("\n── 表单区域文本 ──")
    body = " ".join((tab.ele("tag:body").text or "").split())
    print("   ", body[:500])
finally:
    tab.close()

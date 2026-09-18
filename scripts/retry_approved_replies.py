#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重发队列里 status=approved 但未发出的条目（保守节奏）。"""
import random
import sys
import time

sys.path.insert(0, ".")
import reply_lock as RL  # noqa: E402

items = [s for s in RL.pending() if s.get("status") == "approved"]
print(f"待重发 {len(items)} 条（条间 75-105 秒）\n", flush=True)

sent = failed = 0
for i, s in enumerate(items, 1):
    sid = s.get("id")
    tag = (s.get("name_box") or "")[:24]
    ok, msg = RL.send_approved(sid)
    if ok:
        sent += 1
        print(f"  {i:2}. ✅ {sid} {tag}", flush=True)
    else:
        failed += 1
        print(f"  {i:2}. ❌ {sid} {tag} — {msg}", flush=True)
    if i < len(items):
        rest = random.uniform(75, 105)
        print(f"      ☕ 休息 {rest:.0f}s", flush=True)
        time.sleep(rest)

print(f"\n结果：成功 {sent} / 失败 {failed}", flush=True)
print("投递锁：" + ("🔓 已解除" if RL.release_if_empty() else "🔒 仍有待审/待发条目"), flush=True)

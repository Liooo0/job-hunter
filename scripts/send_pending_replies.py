#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按用户指令「自动回复」批量批准并发送待审回复。

安全设计（沿用 v5 第十二条）：
  · 逐条走 approve(id, 确认措辞) → send_approved(id)，任何一条失败不影响其它条
  · 只发 status=pending 的条目；已 sent/rejected 的不动
  · 发送唯一出口是 hr_auto_reply.send_one（审计留痕）
  · 全部发完后调 release_if_empty()：队列清空才解锁投递
"""
import json
import os
import sys
import time

sys.path.insert(0, ".")
import reply_lock as RL  # noqa: E402

pending = [s for s in RL.pending() if s.get("status") == "pending"]
print(f"待发 {len(pending)} 条\n")

sent = failed = 0
for i, s in enumerate(pending, 1):
    sid = s.get("id")
    tag = f"{(s.get('company') or '')[:16]}/{(s.get('hr_name') or '')[:10]}"
    ok_a, msg_a = RL.approve(sid, "确认")
    if not ok_a:
        print(f"  {i:2}. ⛔ {sid} {tag} 批准失败: {msg_a}")
        continue
    ok_s, msg_s = RL.send_approved(sid)
    if ok_s:
        sent += 1
        print(f"  {i:2}. ✅ {sid} {tag}")
    else:
        failed += 1
        print(f"  {i:2}. ❌ {sid} {tag} 发送失败: {msg_s}")
    time.sleep(2)

print(f"\n结果：成功 {sent} 条 / 失败 {failed} 条")
released = RL.release_if_empty()
print("投递锁：" + ("🔓 已解除（队列已清空）" if released else "🔒 仍锁着（还有待审条目）"))
info = RL.lock_info()
print(f"锁状态: locked={info.get('locked')} pending={info.get('pending_count')}")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等投递轮结束后，用「只认强匹配」的严格规则重试剩下的回复，再收尾。

顺序（同一 Chrome 不得并发）：
  1. 等 platform_51job 退出
  2. 把 unsendable 里有价值的条目改回 approved（会话可能还在，之前一刀切太粗）
  3. 逐条 send（严格规则：强匹配才发，含公司前缀 → 命中即可信；条间 75-105s）
  4. 仍发不出去的落 unsendable（带原因），释放锁
"""
import os
import random
import subprocess
import sys
import time

sys.path.insert(0, ".")
import reply_lock as RL  # noqa: E402

# 1) 等投递轮结束
for i in range(90):
    n = subprocess.run(["bash", "-lc", 'pgrep -f "platform_51job[.]py" | wc -l'],
                       capture_output=True, text=True).stdout.strip()
    if n == "0":
        print(f"投递轮已结束（第 {i} 次检查）", flush=True)
        break
    print(f"等投递轮… {i * 20}s", flush=True)
    time.sleep(20)

# 2) 把 unsendable 改回 approved 重试
back = [s for s in RL.pending() if s.get("status") == "unsendable"]
for s in back:
    RL._update(s.get("id"), status="approved", note="重试：会话可能仍在列表/搜索里")
print(f"\n改回待发重试 {len(back)} 条", flush=True)

# 3) 逐条严格发送
sent = failed = 0
items = [s for s in RL.pending() if s.get("status") == "approved"]
for i, s in enumerate(items, 1):
    ok, msg = RL.send_approved(s.get("id"))
    if ok:
        sent += 1
        print(f"  {i:2}. ✅ {s.get('id')} {(s.get('name_box') or '')[:26]}", flush=True)
    else:
        failed += 1
        print(f"  {i:2}. ❌ {s.get('id')} {(s.get('name_box') or '')[:26]} — {msg}", flush=True)
    if i < len(items):
        rest = random.uniform(75, 105)
        time.sleep(rest)

# 4) 收尾：仍发不出去的落终态 + 释放锁
left = [s for s in RL.pending() if s.get("status") == "approved"]
for s in left:
    RL._update(s.get("id"), status="unsendable",
               note="严格匹配（只认强匹配，宁漏发不发错人）下仍找不到会话；"
                    "Boss 网页仅覆盖 30 天内联系人 → 需人工在 Boss APP 回复。")
print(f"\n本轮：成功 {sent} / 失败 {failed}；落终态 {len(left)} 条", flush=True)
RL.release_if_empty()
# 注意：状态必须看 is_locked()。release_if_empty() 的返回值只表示「本次是否真的删了锁」，
# 锁已被前一个环节释放时它会返回 False，据此打印会误报「仍锁着」（2026-09-18 踩到）。
print("投递锁：" + ("🔒 仍锁着" if RL.is_locked() else "🔓 已释放（无活跃条目）"), flush=True)

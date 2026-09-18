#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回复收尾 + 启动投递（串行，避免同一 Chrome 并发）。

背景（2026-09-18 发现的设计缺陷）：
  REPLY_REVIEW_LOCK 只认 pending/edited/approved 三种「活跃」状态。
  若某条回复因会话已失效（Boss 只保留 30 天内联系人）而永远发不出去，
  它会一直停在 approved → 锁永远不释放 → 投递被无限期挡住。
  所以收尾时必须把「确实发不出去」的条目落到终态 unsendable（带原因），
  锁才会释放，投递才能继续。

流程：等重发批次结束 → 标记 unsendable → 释放锁 → 跑 51job 投递轮。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, ".")
import reply_lock as RL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1) 等重发批次结束
for i in range(60):
    n = subprocess.run(["bash", "-lc", 'pgrep -f "retry_approved_replies[.]py" | wc -l'],
                       capture_output=True, text=True).stdout.strip()
    if n == "0":
        print(f"重发批次已结束（第 {i} 次检查）")
        break
    print(f"等待重发中… {i * 20}s")
    time.sleep(20)

# 2) 把仍停在 approved 的条目标为 unsendable（带原因），否则锁不会释放
left = [s for s in RL.pending() if s.get("status") == "approved"]
if left:
    print(f"\n有 {len(left)} 条重发仍失败，落终态 unsendable（会话可能已过期）：")
    for s in left:
        RL._update(s.get("id"), status="unsendable",
                   note="会话在 Boss 聊天列表/搜索中都找不到（Boss 仅保留 30 天内联系人），"
                        "需人工在 Boss APP 里回复")
        print(f"   · {s.get('id')} {(s.get('name_box') or '')[:24]}")

# 3) 释放锁
released = RL.release_if_empty()
info = RL.lock_info()
print(f"\n投递锁: {'🔓 已释放' if released else '🔒 仍锁着'} (locked={info.get('locked')}, pending={info.get('pending_count')})")

# 4) 跑投递轮（脚本自身也会查 reply_lock，锁没释放就不会投）
if not RL.is_locked():
    print("\n开始 51job 投递轮…")
    r = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "platform_51job.py")],
                       cwd=ROOT, env={**os.environ, "PYTHONPATH": "", "PYTHONPYCACHEPREFIX": "/tmp/jh_pyc"})
    print(f"投递轮结束，退出码 {r.returncode}")
else:
    print("\n锁未释放，本轮不投递（设计行为）")

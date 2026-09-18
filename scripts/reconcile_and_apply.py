#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回复队列收尾（保守版）+ 启动投递。

做三件事：
 1. 把错发记录如实写进队列（不掩盖）
 2. 把发不出去的 approved 条目落终态 unsendable（否则锁永不释放）
 3. 释放锁 → 跑 51job 投递轮
"""
import os
import subprocess
import sys

sys.path.insert(0, ".")
import reply_lock as RL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1) 错发如实记录
WRONG = {
    "R1789185756-0": "⚠️ 2026-09-18 18:45 命中错人：拟发「李女士·聚客科技」，"
                     "实际命中并发送给「李女士·成都市星予创想科技HR」（同名不同公司）。"
                     "根因：弱匹配放行。已修：只认强匹配，宁漏发不发错人。",
}
for sid, note in WRONG.items():
    RL._update(sid, note=note)
    print(f"已记录错发: {sid}")

# 2) 仍停在 approved 的落终态（会话在 Boss 列表/搜索中都找不到）
left = [s for s in RL.pending() if s.get("status") == "approved"]
for s in left:
    RL._update(s.get("id"), status="unsendable",
               note="Boss 聊天列表与搜索（仅覆盖 30 天内联系人）中都找不到该会话；"
                    "疑似会话已过期或 HR 已关闭职位 → 需人工在 Boss APP 里回复。"
                    "为防止「宁漏发不发错人」的新规则下反复空转，落终态。")
    print(f"落终态 unsendable: {s.get('id')} {(s.get('name_box') or '')[:24]}")

# 3) 释放锁
released = RL.release_if_empty()
info = RL.lock_info()
print("\n投递锁：" + ("🔒 仍锁着" if RL.is_locked() else "🔓 已释放（无活跃条目）"))
      f"(locked={info.get('locked')}, pending={info.get('pending_count')})")

if not RL.is_locked():
    print("\n▶ 开始 51job 投递轮…")
    r = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "platform_51job.py")],
                       cwd=ROOT, env={**os.environ, "PYTHONPATH": "",
                                      "PYTHONPYCACHEPREFIX": "/tmp/jh_pyc"})
    print(f"投递轮结束，退出码 {r.returncode}")
else:
    print("\n锁未释放，本轮不投递（设计行为）")

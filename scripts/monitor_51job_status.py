#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""51job 回音监控（给 Hermes cron 的 monitor 字段用）。

为什么单独写一个：cron 的 monitor 靠「本次输出与上次是否相同」决定要不要唤醒 agent。
所以这个脚本的输出**必须是确定性的**（不能有时间戳/随机数），
只把真正会变的信号（已查阅/感兴趣/邀面试的计数与明细）打出来。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANNER = os.path.join(ROOT, "scripts", "scan_51job_status.py")
OUT = os.path.join(ROOT, "data", "51job_status.json")

# 先跑一次只读扫描（会自己落盘 + 必要时推提醒）
try:
    # 必须用 /usr/bin/python3：它才有 DrissionPage + websocket-client
    # （cron 用的解释器不一定装了这些；之前踩过 PATH 变更导致 websocket 崩的坑）
    py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
    subprocess.run([py, SCANNER], cwd=ROOT, timeout=420,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   env={**os.environ, "PYTHONPATH": ""})
except Exception as e:
    print(f"扫描执行失败: {type(e).__name__}")
    sys.exit(0)

try:
    with open(OUT, encoding="utf-8") as f:
        d = json.load(f)
except Exception:
    print("无扫描结果")
    sys.exit(0)


def rows(key):
    return [r for r in (d.get(key) or {}).get("rows", []) if r.get("text")]


def norm(t):
    import re
    return re.sub(r"\s+", " ", t or "").strip()


print(f"已查阅={len(rows('已查阅'))} 感兴趣={len(rows('感兴趣'))} 邀面试={len(rows('邀面试'))}")
for label in ("感兴趣", "邀面试"):
    for r in rows(label)[:6]:
        print(f"[{label}] {norm(r.get('date'))} {norm(r.get('text'))[:90]}")

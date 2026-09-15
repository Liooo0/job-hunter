#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""告警命令行入口（给 run_daily.sh / launchd 用）

用法:
    python3 scripts/notify_alert.py <key> <标题> [正文] [info|warn|error] [节流秒数]

正文里的字面量 \\n 会被还原成换行（shell 双引号里写不了真换行时用）。
未传节流秒数时按级别取默认值：error=不节流、warn=30 分钟、info=6 小时。
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from notify import alert  # noqa: E402

DEFAULT_THROTTLE = {"error": 0, "warn": 1800, "info": 21600}


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) < 2:
        print("用法: python3 scripts/notify_alert.py <key> <标题> [正文] [info|warn|error] [节流秒]")
        return 2

    key = argv[0]
    title = argv[1]
    body = argv[2].replace("\\n", "\n") if len(argv) > 2 else ""
    level = argv[3] if len(argv) > 3 else "warn"
    if level not in DEFAULT_THROTTLE:
        level = "warn"
    throttle = int(argv[4]) if len(argv) > 4 and argv[4].isdigit() else DEFAULT_THROTTLE[level]

    ok = alert(key, title, body, level=level, throttle=throttle)
    print(f"{'已推送' if ok else '未推送'}（key={key}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

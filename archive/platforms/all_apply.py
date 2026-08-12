#!/usr/bin/env python3
"""三平台同步投递: Boss + 51job + 智联，共享同一套 config.json 筛选规则

用法:
  python3 all_apply.py              # 三平台同时开跑
  python3 all_apply.py --only boss  # 仅Boss
  python3 all_apply.py --only 51job # 仅前程无忧
  python3 all_apply.py --only zl    # 仅智联
"""

import subprocess, sys, time, argparse
from pathlib import Path
from datetime import datetime

SKILL_DIR = Path(__file__).parent

PLATFORMS = {
    "boss": {
        "script": "boss_apply.py",
        "label": "Boss直聘",
        "daily_limit": 120,
    },
    "51job": {
        "script": "51job_apply.py",
        "label": "前程无忧",
        "daily_limit": 80,
    },
    "zl": {
        "script": "zhilian_apply.py",
        "label": "智联招聘",
        "daily_limit": 80,
    },
}


def main():
    p = argparse.ArgumentParser(description="三平台同步投递")
    p.add_argument("--only", default=None, choices=["boss", "51job", "zl"])
    p.add_argument("--skip", default=None, help="要跳过的平台, 逗号分隔 (boss,51job,zl)")
    args = p.parse_args()

    # 确定跑哪些
    if args.only:
        todo = {args.only: PLATFORMS[args.only]}
    else:
        todo = dict(PLATFORMS)
        if args.skip:
            for k in args.skip.split(","):
                todo.pop(k.strip(), None)

    print(f"""
╔══════════════════════════════════════════╗
║  🚀 多平台同步投递                        ║
╠══════════════════════════════════════════╣
║  时间: {datetime.now().strftime('%Y-%m-%d %H:%M'):30s} ║
║  平台: {', '.join(v['label'] for v in todo.values()):30s} ║
║  共享配置: config.json                    ║
╚══════════════════════════════════════════╝
""")

    # 依次启动（后续可以改成并行）
    results = {}
    for key, info in todo.items():
        print(f"\n{'='*50}")
        print(f"  ▶️  {info['label']} (--daily, 上限{info['daily_limit']})")
        print(f"{'='*50}")
        try:
            result = subprocess.run(
                [sys.executable, "-u", info["script"], "--daily"],
                cwd=str(SKILL_DIR),
                timeout=3600,  # 最多1小时
            )
            results[key] = "✅" if result.returncode == 0 else f"❌ exit={result.returncode}"
        except subprocess.TimeoutExpired:
            results[key] = "⏰ 超时"

    print(f"""
╔══════════════════════════════════════════╗
║  🏁 全部完成                             ║
╠══════════════════════════════════════════╣""")
    for key, info in todo.items():
        print(f"║  {info['label']:10s}: {results.get(key, '?'):20s} ║")
    print("╚══════════════════════════════════════════╝")


if __name__ == "__main__":
    main()

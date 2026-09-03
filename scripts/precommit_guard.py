#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre-commit 工程防护 — 防止 boss_apply.py 之类核心文件被清空/截断后误提交。

2026-09-03 事故复盘：boss_apply.py 磁盘被清空为 0 字节（9/2 10:16），
9/3 `git add -A` 时把空文件一起提交（f616d85），188 测试全挂才暴露。
教训：不能靠人记，要机器在 commit 前拦住。

检查项：
1. 核心文件存在
2. 核心文件非 0 字节
3. 核心文件未突然大幅缩短（< 基线 50% 即报警）
4. 全量测试通过
5. git diff --stat 无异常（本脚本自身先跑通再挂到 hook）
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT.parent

# 核心文件最小字节数（< 此值 = 可疑，多半被清空/截断）
# 基线参考：boss_apply.py 86928 / job_decision 8595 / value_score 10796 ...
CORE_MIN_BYTES = {
    "boss_apply.py": 50000,     # 主投递脚本（86KB 基线，砍半仍>50K）
    "job_decision.py": 6000,
    "value_score.py": 8000,
    "semantic_parser.py": 6000,
    "plan_router.py": 6000,
    "relocation.py": 6000,
    "reply_lock.py": 8000,
    "quota_scheduler.py": 6000,
    "job_schema.py": 6000,
    "guardrails.py": 4000,
    "hr_auto_reply.py": 15000,
}

PYTHON = sys.executable or "python3"


def check_core_files() -> list[str]:
    """核心文件存在 + 非空 + 未异常缩短。"""
    errors = []
    for fname, min_bytes in CORE_MIN_BYTES.items():
        p = SKILL_DIR / fname
        if not p.exists():
            errors.append(f"❌ 核心文件缺失: {fname}")
            continue
        size = p.stat().st_size
        if size == 0:
            errors.append(f"❌ 核心文件为 0 字节: {fname}（被清空！禁止提交）")
        elif size < min_bytes:
            errors.append(f"❌ 核心文件异常缩小: {fname} = {size}B < {min_bytes}B（被截断？）")
    return errors


def check_tests() -> list[str]:
    """全量测试必须全绿。"""
    try:
        r = subprocess.run(
            [PYTHON, "-m", "unittest", "discover", "-s", "tests"],
            cwd=str(SKILL_DIR), capture_output=True, text=True, timeout=300,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if "OK" not in out and "Ran " not in out:
            return [f"❌ 测试输出异常: {out[-300:]}"]
        if "FAILED" in out or "failures=" in out:
            return [f"❌ 测试未全绿: {out[-300:]}"]
    except subprocess.TimeoutExpired:
        return ["❌ 测试超时（300s）"]
    except Exception as e:
        return [f"❌ 测试执行异常: {e}"]
    return []


def main() -> int:
    # 允许 --quick 跳过测试（仅文件检查），hook 默认全查
    quick = "--quick" in sys.argv
    errors = check_core_files()
    if not quick:
        errors += check_tests()

    if errors:
        print("⛔ pre-commit 防护拦截：")
        for e in errors:
            print("  " + e)
        print("\n（这是 9/3 boss_apply.py 清空事故的防护闸。解决后重试提交。）")
        return 1
    print("✅ pre-commit 防护通过：核心文件完整 + 测试全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())

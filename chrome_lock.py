"""全局 Chrome 互斥锁 —— 同一时间只允许一个投递脚本操作 9223 调试 Chrome。

为什么需要（2026-09-19 事故）：
  各投递脚本原来只有「启动时 pgrep 看一眼」的弱互斥，防不住两种真实情况：
    1) A 先启动、B 后启动 —— 两边启动时都看不到对方，随后互抢标签页；
    2) cron agent 被模型 429 打挂后，脚本失去父进程变成孤儿，继续无人看管地跑。
  实测后果：15:22 那批 51job 有 13 条「按钮未确认:无回执」——投递按钮刚点下，
  页面就被另一个进程的「僵尸页清扫」关掉，回执读空。记录被污染，日限被误算。

设计：fcntl.flock 排他锁（进程崩溃/被杀时内核自动释放，不存在残留死锁）。
  拿不到锁就等待，超时则抛 ChromeBusy —— 调用方必须按「本轮跳过」处理，
  不得继续操作 Chrome。

用法：
    from chrome_lock import chrome_lock, ChromeBusy
    try:
        with chrome_lock("51job", wait_seconds=1800):
            ... 操作 Chrome ...
    except ChromeBusy as e:
        print(f"⏭️ 本轮跳过：{e}")
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

LOCK_FILE = Path(__file__).parent / ".chrome.lock"


class ChromeBusy(RuntimeError):
    """等待超时仍未拿到 Chrome 锁。"""


def _holder_info() -> str:
    try:
        d = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        return f"{d.get('who', '?')}(pid={d.get('pid', '?')}, since {d.get('at', '?')})"
    except Exception:
        return "未知"


@contextmanager
def chrome_lock(who: str, wait_seconds: int = 1800, max_minutes: float | None = None):
    """持有 Chrome 全局锁；拿不到就等待，超时抛 ChromeBusy。

    who         当前脚本标识（写进锁文件，便于排查谁占着）
    wait_seconds 最多等多久（默认 30 分钟；等不到就本轮跳过）
    max_minutes  可选：持锁期间的最长运行时间，到点自杀（防孤儿进程无限跑）
    """
    LOCK_FILE.touch(exist_ok=True)
    fh = open(LOCK_FILE, "r+")
    deadline = time.time() + wait_seconds
    waited_logged = False

    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() >= deadline:
                fh.close()
                raise ChromeBusy(
                    f"等待 {wait_seconds}s 仍未拿到 Chrome 锁，当前持有者 {_holder_info()}"
                )
            if not waited_logged:
                print(f"  ⏳ Chrome 被占用（{_holder_info()}），等待中…")
                waited_logged = True
            time.sleep(5)

    # 写持有者信息（诊断用；失败不影响加锁）
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({
            "who": who, "pid": os.getpid(),
            "at": datetime.now().strftime("%H:%M:%S"),
        }, ensure_ascii=False))
        fh.flush()
    except Exception:
        pass

    stop = threading.Event()
    if max_minutes:
        def _watchdog():
            if not stop.wait(max_minutes * 60):
                print(f"\n⏰ {who} 超过 {max_minutes} 分钟上限，自杀退出（防孤儿）",
                      file=sys.stderr, flush=True)
                os._exit(124)
        threading.Thread(target=_watchdog, daemon=True).start()

    try:
        yield
    finally:
        stop.set()
        try:
            fh.seek(0); fh.truncate(); fh.flush()
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


# ── 非上下文版本 ────────────────────────────────────────────────
# 适合插在既有 main() 里、不想改动 try/finally 缩进的场合。
# flock 在进程退出（含崩溃/被 kill）时由内核自动释放，所以只需持有到进程结束，
# 不需要显式 release —— 但务必保留模块级引用，否则 fh 被 GC 会提前解锁。
_fh_holder: list = []


def acquire(who: str, wait_seconds: int = 1800, max_minutes: float | None = None) -> bool:
    """拿到 Chrome 全局锁返回 True（持有到进程退出）；超时返回 False。

    返回 False 时调用方必须按「本轮跳过」处理，绝不能继续操作 Chrome。
    """
    LOCK_FILE.touch(exist_ok=True)
    fh = open(LOCK_FILE, "r+")
    deadline = time.time() + wait_seconds
    waited = False

    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() >= deadline:
                fh.close()
                print(f"  ⏳ 等待 {wait_seconds}s 未拿到 Chrome 锁"
                      f"（持有者 {_holder_info()}），本轮跳过")
                return False
            if not waited:
                print(f"  ⏳ Chrome 被占用（{_holder_info()}），等待中…")
                waited = True
            time.sleep(5)

    try:
        fh.seek(0); fh.truncate()
        fh.write(json.dumps({"who": who, "pid": os.getpid(),
                             "at": datetime.now().strftime("%H:%M:%S")},
                            ensure_ascii=False))
        fh.flush()
    except Exception:
        pass

    _fh_holder.append(fh)   # 防 GC 提前解锁

    if max_minutes:
        stop = threading.Event()

        def _watchdog():
            if not stop.wait(max_minutes * 60):
                print(f"\n⏰ {who} 超过 {max_minutes} 分钟上限，自杀退出（防孤儿）",
                      file=sys.stderr, flush=True)
                os._exit(124)
        threading.Thread(target=_watchdog, daemon=True).start()

    return True


"""chrome_lock 回归用例（2026-09-21 补：该模块此前**零测试覆盖**）

它守的是 9/19 那次数据污染事故：两个投递进程同时操作 9223 Chrome，
投递按钮刚点下就被另一个进程的僵尸页清扫关掉 → 13 条「按钮无回执」→ 记录污染、日限误算。

因此测试必须用**真进程跨进程抢锁**，不能 mocked 掉 —— 要验的正是内核级 flock 在
跨进程下是否真的互斥、是否真的在进程退出时自动释放。
"""
import json
import os
import subprocess
import sys
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import chrome_lock as CL  # noqa: E402

_HOLD = ("import chrome_lock,time;"
         "print('HELD', chrome_lock.acquire({who!r}, wait_seconds=10), flush=True);"
         "time.sleep({hold})")
_TRY = ("import chrome_lock,time;t=time.time();"
        "ok=chrome_lock.acquire('测试B', wait_seconds={wait});"
        "print('RESULT', ok, round(time.time()-t,1))")
_TRY_CTX = ("import chrome_lock,time;"
            "from chrome_lock import chrome_lock, ChromeBusy;t=time.time();"
            "\ntry:\n"
            "    with chrome_lock('测试B', wait_seconds={wait}):\n"
            "        print('RESULT acquired', round(time.time()-t,1))\n"
            "except ChromeBusy:\n"
            "    print('RESULT BUSY', round(time.time()-t,1))")


def _spawn(code):
    return subprocess.Popen([sys.executable, "-c", code], cwd=_ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _out(p):
    return (p.communicate()[0] or "").strip()


class TestChromeLockAcrossProcesses(unittest.TestCase):
    def setUp(self):
        self._procs = []
        # 前置：确认锁是空闲的（若被别人占着 → 跳过而不是误报失败）
        p = _spawn("import chrome_lock;print(chrome_lock.acquire('预检', wait_seconds=1))")
        self._procs.append(p)
        if "True" not in _out(p):
            self.skipTest("Chrome 锁正被其他进程占用（例如投递轮在跑），跳过以免误报")

    def tearDown(self):
        for p in self._procs:
            if p.poll() is None:
                p.kill()

    # ── 互斥 ──
    def test_second_process_waits_until_holder_exits(self):
        a = _spawn(_HOLD.format(who="测试A", hold=3))
        self._procs.append(a)
        time.sleep(0.8)
        b = _spawn(_TRY.format(wait=20))
        self._procs.append(b)
        out = _out(b)
        self.assertIn("RESULT True", out, f"B 应当等到 A 释放后拿到锁：{out}")
        waited = float(out.split()[-1])
        self.assertGreaterEqual(waited, 1.0, f"B 没有真的被挡住（只等了 {waited}s）：{out}")

    # ── 超时：必须及时返回 False，且不能因 5s 轮询周期被拖长 ──
    def test_timeout_returns_false_promptly(self):
        a = _spawn(_HOLD.format(who="测试A", hold=8))
        self._procs.append(a)
        time.sleep(0.8)
        b = _spawn(_TRY.format(wait=1))
        self._procs.append(b)
        out = _out(b)
        self.assertIn("RESULT False", out, f"超时应当返回 False：{out}")
        waited = float(out.split()[-1])
        self.assertLess(waited, 3.0, f"超时判定被轮询周期拖长（等了 {waited}s）：{out}")

    def test_context_manager_raises_chrome_busy(self):
        a = _spawn(_HOLD.format(who="测试A", hold=8))
        self._procs.append(a)
        time.sleep(0.8)
        b = _spawn(_TRY_CTX.format(wait=1))
        self._procs.append(b)
        self.assertIn("RESULT BUSY", _out(b), "上下文版本拿不到锁必须抛 ChromeBusy")

    # ── 释放语义 ──
    def test_kernel_releases_lock_after_holder_dies(self):
        a = _spawn(_HOLD.format(who="测试A", hold=1))
        self._procs.append(a)
        a.wait(timeout=15)
        self.assertTrue(CL.acquire("退出后检查", wait_seconds=10),
                        "持有者进程退出后，内核应当自动释放 flock")

    # ── 持有者信息（诊断用） ──
    def test_holder_info_is_recorded(self):
        a = _spawn(_HOLD.format(who="测试持有者", hold=4))
        self._procs.append(a)
        time.sleep(0.8)
        d = json.loads(CL.LOCK_FILE.read_text(encoding="utf-8"))
        self.assertEqual(d.get("who"), "测试持有者")
        self.assertTrue(d.get("pid"))
        self.assertTrue(d.get("at"))


if __name__ == "__main__":
    unittest.main()

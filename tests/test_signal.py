#!/usr/bin/env python3
"""boss_apply 信号处理单元测试（P2-T2 / A6）。

验证点：
- SIGINT（Ctrl+C）：只置 SHOULD_STOP，不写暂停锁（临时目录断言）；
- SIGHUP/SIGTERM：保持旧行为，写暂停锁。

说明：无法在单测里真起投递进程（会连 Chrome/真实网站），
这里直接调用 _signal_handler 验证分支逻辑；端到端行为
「终端 Ctrl+C 后 .paused 不出现」由人工在下次手动运行时确认。
真实仓库的 .paused 全程不碰 —— PAUSE_FILE 被重定向到临时目录。

跑法一（unittest discover，从项目根目录）:
    PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -t . -v

跑法二（直接运行）:
    cd tests && PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 test_signal.py
"""
import signal
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import boss_apply  # noqa: E402  # import 无副作用（已验证：不起浏览器、不写文件）


class TestSignalHandler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_pause_file = boss_apply.PAUSE_FILE
        boss_apply.PAUSE_FILE = Path(self.tmp.name) / ".paused"
        boss_apply.SHOULD_STOP = False
        boss_apply.STOP_REASON = ""

    def tearDown(self):
        boss_apply.PAUSE_FILE = self._orig_pause_file
        boss_apply.SHOULD_STOP = False

    def test_sigint_stops_without_pause_lock(self):
        boss_apply._signal_handler(signal.SIGINT, None)
        self.assertTrue(boss_apply.SHOULD_STOP)
        self.assertFalse(boss_apply.PAUSE_FILE.exists(), "SIGINT 不应写暂停锁")

    def test_sigterm_writes_pause_lock(self):
        boss_apply._signal_handler(signal.SIGTERM, None)
        self.assertTrue(boss_apply.SHOULD_STOP)
        self.assertTrue(boss_apply.PAUSE_FILE.exists(), "SIGTERM 应保持写锁防 launchd 重拉")

    def test_sighup_writes_pause_lock(self):
        boss_apply._signal_handler(signal.SIGHUP, None)
        self.assertTrue(boss_apply.SHOULD_STOP)
        self.assertTrue(boss_apply.PAUSE_FILE.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

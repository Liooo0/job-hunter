#!/usr/bin/env python3
"""--recovery 解封恢复期命令回归测试（v2.1 稳定性测试）。

验证点：
1. `python3 boss_apply.py --recovery 3` 不崩溃，且把截止时间写入 .recovery_until；
2. 写入的截止时间与"今天 + N 天"一致（精度到日期），天数解析正确；
3. `--recovery`（不带值，auto）回退到 config.safety.recovery_days；
4. 非法天数（如 "abc"）回退到 config 默认而非报错（边界情况）；
5. is_recovery_active() 能识别刚写入的恢复期（与读取端闭环）。

历史 Bug：boss_apply.py 只 `from datetime import datetime`，而 main() 的
--recovery 分支使用 `datetime.now() + timedelta(days=days)`，触发
NameError: name 'timedelta' is not defined —— 该命令自 2026-08-17 起从未成功执行过。

跑法:
    PYTHONPYCACHEPREFIX=/tmp/jh_pyc python3 -m unittest tests.test_v21_recovery_flag -v
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import boss_apply  # noqa: E402

_CFG = {
    "safety": {
        "recovery_days": 3,
        "recovery_daily_cap": 25,
        "normal_daily_cap": 50,
        "hourly_cap": 8,
        "night_ban_start": 22,
        "night_ban_end": 8,
        "dedup_days": 7,
    }
}


class RecoveryFlagTest(unittest.TestCase):
    """main() 的 --recovery 分支：全程 mock，不碰真实 DB/Chrome/技能目录文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.recovery_file = Path(self._tmp.name) / ".recovery_until"

    def _run_main(self, argv_tail):
        with mock.patch.object(boss_apply, "RECOVERY_FILE", self.recovery_file), \
             mock.patch.object(boss_apply, "load_config", return_value=dict(_CFG)), \
             mock.patch.object(boss_apply, "ensure_migrated", lambda **kw: None), \
             mock.patch.object(sys, "argv", ["boss_apply.py"] + argv_tail):
            boss_apply.main()

    def test_recovery_sets_file_without_crash(self):
        """核心缺陷路径：修复前此用例以 NameError 失败。"""
        self._run_main(["--recovery", "3"])
        self.assertTrue(self.recovery_file.exists(),
                        "--recovery 3 应写入 .recovery_until")
        until = datetime.fromisoformat(self.recovery_file.read_text().strip())
        expected = datetime.now() + timedelta(days=3)
        self.assertEqual(until.date(), expected.date(),
                         f"截止时间应约为3天后，实际 {until.isoformat()}")
        self.assertGreater(until, datetime.now(), "恢复期截止时间必须在未来")

    def test_recovery_auto_uses_config_days(self):
        """不带值 → auto → 取 config.safety.recovery_days（=3）。"""
        self._run_main(["--recovery"])
        until = datetime.fromisoformat(self.recovery_file.read_text().strip())
        self.assertEqual(until.date(), (datetime.now() + timedelta(days=3)).date())

    def test_recovery_invalid_days_falls_back(self):
        """边界：非数字天数不崩溃，回退到 config 默认 3 天。"""
        self._run_main(["--recovery", "abc"])
        until = datetime.fromisoformat(self.recovery_file.read_text().strip())
        self.assertEqual(until.date(), (datetime.now() + timedelta(days=3)).date())

    def test_written_recovery_period_is_active_when_read(self):
        """闭环：写入后 is_recovery_active() 必须判定为恢复期（降量上限生效前提）。"""
        self._run_main(["--recovery", "2"])
        with mock.patch.object(boss_apply, "RECOVERY_FILE", self.recovery_file):
            self.assertTrue(boss_apply.is_recovery_active(),
                            "刚设置的恢复期应被判定为激活")
        # 边界：文件过期 → 自动回到正常期
        expired = datetime.now() - timedelta(seconds=1)
        self.recovery_file.write_text(expired.isoformat())
        with mock.patch.object(boss_apply, "RECOVERY_FILE", self.recovery_file):
            self.assertFalse(boss_apply.is_recovery_active(),
                             "过期的恢复期不应继续降量")


if __name__ == "__main__":
    unittest.main(verbosity=2)

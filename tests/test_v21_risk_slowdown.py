#!/usr/bin/env python3
"""v2.1 任务二：风控阶梯降速（Risk Slowdown）纯函数单元测试——离线跑。

覆盖任务书要求的三类序列 + 失败率规则：
- 首次降速：第一次 uncertain → 下一次投递前间隔 ×factor（不 stop）
- 成功复位：下一次 verified 成功 → 计数清零、恢复 multiplier=1.0
- 连续 2 次 uncertain → stop（提前收工）
- 尝试 ≥10 次且 (failed+uncertain)/attempts > 30% → stop
"""
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import risk_slowdown  # noqa: E402


class TestNormalPathUntouched(unittest.TestCase):
    def test_all_applied_no_stop_normal_interval(self):
        st = risk_slowdown.evaluate(["applied"] * 20)
        self.assertFalse(st["stop"])
        self.assertEqual(st["next_interval_multiplier"], 1.0)
        self.assertEqual(st["consecutive_uncertain"], 0)

    def test_empty_events(self):
        st = risk_slowdown.evaluate([])
        self.assertFalse(st["stop"])
        self.assertEqual(st["next_interval_multiplier"], 1.0)

    def test_single_failure_below_rate_threshold(self):
        # failed 不触发连击降速（连击只由 uncertain 驱动），也不够失败率门槛
        st = risk_slowdown.evaluate(["applied", "failed", "applied"])
        self.assertFalse(st["stop"])
        self.assertEqual(st["next_interval_multiplier"], 1.0)


class TestFirstUncertainSlowdown(unittest.TestCase):
    def test_first_uncertain_doubles_next_interval_no_stop(self):
        st = risk_slowdown.evaluate(["applied", "uncertain"])
        self.assertFalse(st["stop"])
        self.assertEqual(st["next_interval_multiplier"], 2.0)  # 默认 factor
        self.assertEqual(st["consecutive_uncertain"], 1)

    def test_custom_factor_respected(self):
        st = risk_slowdown.evaluate(["uncertain"], factor=3.5)
        self.assertFalse(st["stop"])
        self.assertAlmostEqual(st["next_interval_multiplier"], 3.5)


class TestVerifiedSuccessResets(unittest.TestCase):
    def test_uncertain_then_applied_resets(self):
        st = risk_slowdown.evaluate(["uncertain", "applied"])
        self.assertFalse(st["stop"])
        self.assertEqual(st["consecutive_uncertain"], 0)     # 计数清零
        self.assertEqual(st["next_interval_multiplier"], 1.0)  # 恢复正常间隔

    def test_recovered_then_uncertain_again_slowdowns_again(self):
        st = risk_slowdown.evaluate(["uncertain", "applied", "uncertain"])
        self.assertFalse(st["stop"])
        self.assertEqual(st["consecutive_uncertain"], 1)
        self.assertEqual(st["next_interval_multiplier"], 2.0)

    def test_failed_between_uncertains_neither_clears_nor_extends_streak(self):
        # failed 不清零也不累加连击；multiplier 只看最近一次事件
        st = risk_slowdown.evaluate(["uncertain", "failed"])
        self.assertFalse(st["stop"])
        self.assertEqual(st["consecutive_uncertain"], 1)
        self.assertEqual(st["next_interval_multiplier"], 1.0)


class TestConsecutiveStop(unittest.TestCase):
    def test_two_consecutive_uncertain_stop(self):
        st = risk_slowdown.evaluate(["applied", "uncertain", "uncertain"])
        self.assertTrue(st["stop"])
        self.assertIn("UNCERTAIN", st["reason"])
        self.assertEqual(st["consecutive_uncertain"], 2)

    def test_three_consecutive_uncertain_stop(self):
        st = risk_slowdown.evaluate(["uncertain"] * 3)
        self.assertTrue(st["stop"])

    def test_max_consecutive_configurable(self):
        # max=3 时两次 uncertain 不停
        st = risk_slowdown.evaluate(["uncertain", "uncertain"],
                                    max_consecutive_uncertain=3)
        self.assertFalse(st["stop"])
        self.assertEqual(st["consecutive_uncertain"], 2)


class TestFailureRateRule(unittest.TestCase):
    def _seq(self, applied, bad):
        return ["applied"] * applied + ["failed"] * bad

    def test_over_30_percent_after_10_attempts_stops(self):
        seq = self._seq(6, 4)  # 10 次，坏 4 个 = 40% > 30%
        st = risk_slowdown.evaluate(seq)
        self.assertTrue(st["stop"])
        self.assertIn("40%", st["reason"])

    def test_exactly_30_percent_does_not_stop(self):
        seq = self._seq(7, 3)  # 10 次，坏 3 个 = 30%，不大于阈值
        st = risk_slowdown.evaluate(seq)
        self.assertFalse(st["stop"])

    def test_under_10_attempts_never_trips_rate_rule(self):
        seq = self._seq(2, 4)  # 6 次里坏 4 个 = 67%，但尝试数不足
        st = risk_slowdown.evaluate(seq)
        self.assertFalse(st["stop"])

    def test_rate_rule_with_min_attempts_override(self):
        seq = self._seq(1, 2)  # 3 次坏 2 个
        st = risk_slowdown.evaluate(seq, min_attempts=3, failure_rate_stop=0.5)
        self.assertTrue(st["stop"])

    def test_unknown_event_values_ignored(self):
        st = risk_slowdown.evaluate(["applied", "weird", "", None])
        self.assertFalse(st["stop"])
        self.assertEqual(st["consecutive_uncertain"], 0)

    def test_none_input(self):
        st = risk_slowdown.evaluate(None)
        self.assertFalse(st["stop"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

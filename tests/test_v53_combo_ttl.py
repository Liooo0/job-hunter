#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合轮次 TTL 测试（2026-09-11）。

背景：job-hunter 的 Boss 端断点 done_combos 跑满一轮后 total_applied_this_round 归 0，
      从此每天只 SKIPPED 不投递，而 cron 依旧报 ok（静默死锁）。
      修法：轮次超过 TTL(72h) 视为新一轮，清空组合集合。
"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import reply_lock  # noqa: E402


class TestComboRoundTTL(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 15, 8, 0, 0)
        self.done = ["深圳×AI应用工程师", "广州×AI实施"]

    # ── 年龄计算 ──
    def test_age_none_or_bad_returns_negative(self):
        self.assertEqual(reply_lock.combo_round_age_hours(None, now=self.now), -1.0)
        self.assertEqual(reply_lock.combo_round_age_hours("", now=self.now), -1.0)
        self.assertEqual(reply_lock.combo_round_age_hours("乱码", now=self.now), -1.0)

    def test_age_computed_in_hours(self):
        saved = (self.now - timedelta(hours=5)).isoformat(timespec="seconds")
        self.assertAlmostEqual(reply_lock.combo_round_age_hours(saved, now=self.now), 5.0, places=3)

    # ── 重置判定 ──
    def test_not_reset_when_empty(self):
        """空组合集合 = 本轮还没开始，不该触发重置。"""
        saved = (self.now - timedelta(hours=999)).isoformat()
        self.assertFalse(reply_lock.should_reset_combo_round([], saved, now=self.now))

    def test_not_reset_within_ttl(self):
        saved = (self.now - timedelta(hours=71)).isoformat(timespec="seconds")
        self.assertFalse(reply_lock.should_reset_combo_round(self.done, saved, now=self.now))

    def test_reset_after_ttl(self):
        saved = (self.now - timedelta(hours=73)).isoformat(timespec="seconds")
        self.assertTrue(reply_lock.should_reset_combo_round(self.done, saved, now=self.now))

    def test_reset_exactly_at_boundary(self):
        saved = (self.now - timedelta(hours=72)).isoformat(timespec="seconds")
        self.assertTrue(reply_lock.should_reset_combo_round(self.done, saved, now=self.now))

    def test_no_reset_when_saved_at_unparsable(self):
        """时间戳缺失/损坏 → 保守不动断点（宁可少投一班，不能重复投）。"""
        self.assertFalse(reply_lock.should_reset_combo_round(self.done, None, now=self.now))
        self.assertFalse(reply_lock.should_reset_combo_round(self.done, "坏时间", now=self.now))

    def test_ttl_configurable(self):
        saved = (self.now - timedelta(hours=10)).isoformat(timespec="seconds")
        self.assertFalse(reply_lock.should_reset_combo_round(self.done, saved, ttl_hours=72, now=self.now))
        self.assertTrue(reply_lock.should_reset_combo_round(self.done, saved, ttl_hours=6, now=self.now))

    def test_deadlock_scenario_real_world(self):
        """真实场景复现：2026-09-11 13:08 保存的 16 组合，到 2026-09-15 08:00 已静默 3 天多 →
        必须触发重置，否则 Boss 端继续投 0 条。"""
        saved = "2026-09-11T13:08:52"
        done = [f"{c}×{k}" for c in ("深圳", "广州") for k in
                ("AI应用工程师", "AI实施", "AI解决方案", "RPA开发", "大模型应用", "智能体", "AI团队", "AI产品")]
        self.assertEqual(len(done), 16)
        self.assertTrue(reply_lock.should_reset_combo_round(done, saved, now=self.now))


if __name__ == "__main__":
    unittest.main()

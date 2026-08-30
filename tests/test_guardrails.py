#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guardrails v1.0 回归测试 —— 安全配置被改松时必须能拦住（模型无关）。"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import guardrails as GR


def base_cfg():
    return {
        "line": "ai",
        "safety": {
            "night_ban_start": 22,
            "night_ban_end": 8,
            "normal_daily_cap": 50,
            "hourly_cap": 8,
        },
        "salary_filter": {"home_min_accept": 10, "away_min_accept": 10},
        "body_exclude_keywords": ["单休", "大小周", "996", "夜班", "加班"],
        "exclude_keywords": ["实习", "实习生", "销售"],
    }


class TestGuardrails(unittest.TestCase):
    def test_pass_ok_config(self):
        self.assertEqual(GR.run_all(base_cfg()), [])

    def test_salary_loosened_to_8k(self):
        c = base_cfg()
        c["salary_filter"]["home_min_accept"] = 8
        v = GR.run_all(c)
        self.assertTrue(any("薪资" in x for x in v))

    def test_weekend_word_removed(self):
        c = base_cfg()
        c["body_exclude_keywords"] = ["单休", "大小周"]  # 丢了 996/夜班
        v = GR.run_all(c)
        self.assertTrue(any("双休" in x or "996" in x or "夜班" in x for x in v))

    def test_intern_word_removed(self):
        c = base_cfg()
        c["exclude_keywords"] = ["实习"]  # 丢了 实习生
        v = GR.run_all(c)
        self.assertTrue(any("实习" in x for x in v))

    def test_night_ban_relaxed(self):
        c = base_cfg()
        c["safety"]["night_ban_start"] = 23
        v = GR.run_all(c)
        self.assertTrue(any("夜" in x for x in v))

    def test_daily_cap_over_limit(self):
        c = base_cfg()
        c["safety"]["normal_daily_cap"] = 200
        v = GR.run_all(c)
        self.assertTrue(any("日上限" in x for x in v))

    def test_hourly_cap_over_limit(self):
        c = base_cfg()
        c["safety"]["hourly_cap"] = 30
        v = GR.run_all(c)
        self.assertTrue(any("时上限" in x for x in v))

    def test_procurement_line_allows_8k(self):
        c = base_cfg()
        c["line"] = "procurement"
        c["salary_filter"] = {"home_min_accept": 8, "away_min_accept": 8}
        self.assertEqual(GR.run_all(c), [])

    def test_real_config_passes(self):
        p = Path(__file__).resolve().parent.parent / "config.json"
        cfg = json.loads(p.read_text())
        self.assertEqual(GR.run_all(cfg), [])


if __name__ == "__main__":
    unittest.main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guardrails v2.0 回归测试 —— 安全配置被改松时必须能拦住（模型无关）。"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import guardrails as GR
import job_decision as JD


def base_cfg():
    return {
        "safety": {
            "night_ban_start": 22,
            "night_ban_end": 8,
            "normal_daily_cap": 50,
            "hourly_cap": 8,
        },
        "salary_bands": {"hard_floor": 5, "normal_floor": 8, "priority": 10},
        "body_exclude_keywords": ["单休", "大小周", "996", "夜班", "加班"],
        "exclude_keywords": ["实习", "实习生", "销售"],
    }


class TestGuardrails(unittest.TestCase):
    # ── A1 系统级安全 ──
    def test_pass_ok_config(self):
        self.assertEqual(GR.run_all(base_cfg()), [])

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

    # ── A2 分层薪资参数（v2.0: 校验分层不被改松，不再是 1w 硬线） ──
    def test_salary_floors_ok(self):
        self.assertEqual(GR.check_salary(base_cfg()), [])

    def test_hard_floor_loosened_below_5k(self):
        c = base_cfg()
        c["salary_bands"] = {"hard_floor": 4, "normal_floor": 8, "priority": 10}
        v = GR.run_all(c)
        self.assertTrue(any("地板" in x for x in v))

    def test_priority_loosened_below_10k(self):
        c = base_cfg()
        c["salary_bands"] = {"hard_floor": 5, "normal_floor": 8, "priority": 9}
        v = GR.run_all(c)
        self.assertTrue(any("优先档" in x for x in v))

    def test_stricter_floors_pass(self):
        c = base_cfg()
        c["salary_bands"] = {"hard_floor": 6, "normal_floor": 9, "priority": 12}  # 更严 = 允许
        self.assertEqual(GR.run_all(c), [])

    # ── A3 词表完整性 ──
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

    # ── B 决策器完整性 ──
    def test_decisioner_intact(self):
        self.assertEqual(GR.check_decisioner(base_cfg()), [])

    def test_decisioner_works_on_key_cases(self):
        # 五类岗位抽样：南方电网低薪编制=放行；12K外包大小周=制度死（外包红线优先）
        d1 = JD.evaluate_job("南方电网", "数据运维值班员", "正式编制,五险一金齐全,稳定", "5-7K")
        self.assertEqual(d1.action, "ALLOW")
        self.assertEqual(d1.priority, "LOW")
        d2 = JD.evaluate_job("某外包公司", "测试驻场", "华为驻场,大小周", "12-16K")
        self.assertEqual(d2.action, "ALLOW")  # 12K大小周冲刺特批；外包拦截归deep_filter层


if __name__ == "__main__":
    unittest.main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RULES_v2.0 岗位价值决策器回归测试 —— 分层薪资 + 特批通道 + 制度红线。

用例来源 = 用户定稿的五类岗位 + 历史上真实误判案例。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from job_decision import evaluate_job, parse_salary_low, Decision


class TestSalaryParse(unittest.TestCase):
    def test_standard_k(self):
        self.assertEqual(parse_salary_low("12-20K"), 12.0)

    def test_wan_format(self):
        self.assertEqual(parse_salary_low("1.2-2万"), 12.0)

    def test_daily_wage(self):
        self.assertAlmostEqual(parse_salary_low("200-300元/天"), 200 * 22 / 1000)

    def test_unknown_returns_0(self):
        self.assertEqual(parse_salary_low("面议"), 0.0)
        self.assertEqual(parse_salary_low(""), 0.0)


class TestSalaryBands(unittest.TestCase):
    def test_over_10k_high_priority(self):
        d = evaluate_job("某科技", "AI应用工程师", "RAG知识库开发", "12-20K")
        self.assertEqual((d.action, d.priority), ("ALLOW", "HIGH"))

    def test_8_10k_normal(self):
        d = evaluate_job("某科技", "AI实施工程师", "企业AI部署交付", "9-12K")
        self.assertEqual((d.action, d.priority), ("ALLOW", "NORMAL"))

    def test_5_8k_no_special_reject(self):
        # 2026-09-05 冲刺模式：8K 也干（用户确认），5-8K 直接 ALLOW-LOW 不再要特批
        d = evaluate_job("某外包", "采购专员", "跟单", "6-8K")
        self.assertEqual(d.action, "ALLOW")
        self.assertEqual(d.priority, "LOW")

    def test_under_5k_reject(self):
        d = evaluate_job("某公司", "数据标注员", "标注", "4-6K")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("<5K", d.reason)

    def test_unknown_salary_not_rejected(self):
        d = evaluate_job("某Agent科技", "Agent应用工程师", "Agent工作流开发", "面议")
        self.assertEqual(d.action, "ALLOW")
        self.assertEqual(d.salary_band, "unknown")


class TestSpecialApproval(unittest.TestCase):
    """用户点名场景：低薪但高质量岗位必须有正式例外通道，不能靠模型临场发挥。"""

    def test_south_grid_5_7k_formal_approval(self):
        # 冲刺模式：5-8K 直接可投（LOW），编制信号仍进 reason
        d = evaluate_job("南方电网", "数据运维值班员", "正式编制,五险一金齐全,稳定", "5-7K")
        self.assertEqual(d.action, "ALLOW")
        self.assertEqual(d.priority, "LOW")

    def test_state_owned_admin_6_8k_approval(self):
        d = evaluate_job("某国企", "行政助理", "央企正式工,双休", "6-8K")
        self.assertEqual(d.action, "ALLOW")

    def test_private_6_8k_no_approval(self):
        d = evaluate_job("某私企", "行政助理", "大小周", "6-8K")
        self.assertEqual(d.action, "REJECT")  # 制度红线优先


class TestHardRedlines(unittest.TestCase):
    def test_single_rest_high_salary_still_reject(self):
        # 冲刺模式：大小周 12K+ 可谈（不再一票否决），但单休/996 高薪仍拒（真红线）
        d = evaluate_job("某科技", "AI应用工程师", "大小周", "15-20K")
        self.assertEqual(d.action, "ALLOW")  # 15K 大小周 → 冲刺特批
        d2 = evaluate_job("某科技", "AI应用工程师", "单休", "15-20K")
        self.assertEqual(d2.action, "REJECT")  # 单休高薪仍死
        d3 = evaluate_job("某科技", "AI应用工程师", "996", "15-20K")
        self.assertEqual(d3.action, "REJECT")  # 996 高薪仍死

    def test_night_shift_reject(self):
        d = evaluate_job("某公司", "运维工程师", "需要上夜班轮值", "14-18K")
        self.assertEqual(d.action, "REJECT")

    def test_shift_rota_reject(self):
        d = evaluate_job("某公司", "测试工程师", "三班倒", "10-15K")
        self.assertEqual(d.action, "REJECT")

    def test_intern_always_reject(self):
        d = evaluate_job("某公司", "AI实习生", "协助开发", "200元/天")
        self.assertEqual(d.action, "REJECT")

    def test_hr_company_reject(self):
        d = evaluate_job("某人力资源服务公司", "AI开发工程师", "开发", "10-15K")
        self.assertEqual(d.action, "REJECT")

    def test_parsed_signals_override(self):
        # 模型/解析层提供结构化信号 → 直接采纳（模型看懂，代码判死）
        d = evaluate_job("某公司", "AI应用工程师", "具体职责未写", "12-20K",
                         parsed_signals={"workday": "single_rest"})
        self.assertEqual(d.action, "REJECT")
        d2 = evaluate_job("某公司", "AI应用工程师", "具体职责未写", "12-20K",
                          parsed_signals={"workday": "double_rest"})
        self.assertEqual(d2.action, "ALLOW")


if __name__ == "__main__":
    unittest.main()
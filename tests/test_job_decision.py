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
        # 2026-09-05 定稿：底薪≥8K才投。区间5-8K无底薪声明/底薪<8K → 拒
        d = evaluate_job("某外包", "采购专员", "跟单", "6-8K")
        self.assertEqual(d.action, "REJECT")

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

class TestPuaSalary(unittest.TestCase):
    """2026-09-05 回归：Boss 图标字体 PUA 薪资字符必须能解码，否则 12K 大小周特批失效。"""

    def test_pua_digits_decoded(self):
        from job_decision import parse_salary_low
        # \ue032\ue039 = "29"（0xe030=0, 0xe039=9）
        self.assertEqual(parse_salary_low("\ue032\ue039-\ue034\ue031K"), 29.0)
        self.assertEqual(parse_salary_low("\ue035\ue030-\ue031\ue030\ue030K"), 50.0)

    def test_pua_salary_triggers_daxiaozhou_waiver(self):
        # 18-26K 大小周 → PUA 解码后 ≥12K → 特批放行（不超30K红线）
        d = evaluate_job("优必选", "AI专家", "大小周", "\ue031\ue038-\ue032\ue036K", city="深圳")
        self.assertEqual(d.action, "ALLOW")

    def test_pua_low_salary_daxiaozhou_still_reject(self):
        # 4-6K 大小周 → 仍拒（特批只覆盖 ≥12K）
        d = evaluate_job("某司", "AI漫剧", "大小周", "\ue034-\ue036K", city="广州")
        self.assertEqual(d.action, "REJECT")


class TestBaseSalaryPolicy(unittest.TestCase):
    """2026-09-05 用户定稿：底薪≥8K才投 + 狼性文化排除。"""

    def test_low_interval_with_low_base_reject(self):
        # 聚客案：标7-22K实底薪5K → 拒
        d = evaluate_job("聚客科技", "AI大模型训练", "底薪5000+高提成", "7-22K", city="深圳")
        self.assertEqual(d.action, "REJECT")

    def test_4k_base_reject(self):
        # 征川案：标4-9K底薪4K → 拒
        d = evaluate_job("征川文化", "AIGC剪辑", "底薪4K", "4-9K", city="杭州")
        self.assertEqual(d.action, "REJECT")

    def test_high_base_in_low_interval_allow(self):
        # 标6-10K但写明底薪8K+提成 → 放行（底薪达标）
        d = evaluate_job("某公司", "AI销售", "底薪8K+高提成,双休", "6-10K", city="深圳")
        self.assertEqual(d.action, "ALLOW")

    def test_wolf_culture_reject(self):
        d = evaluate_job("某公司", "AI实施", "狼性文化,多劳多得", "10-15K", city="深圳")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("狼性", d.reason)


class TestSalaryCap30K(unittest.TestCase):
    """2026-09-05 用户定稿：超过 30K 不投，不真实（虚高画饼）。"""

    def test_over_30k_reject(self):
        d = evaluate_job("快手", "AI Agent研发", "双休", "\ue035\ue031-\ue038\ue031K", city="杭州")  # 51-81K
        self.assertEqual(d.action, "REJECT")
        self.assertIn("30K", d.reason)

    def test_29_41k_reject(self):
        d = evaluate_job("某司", "AI应用", "双休", "\ue032\ue039-\ue034\ue031K", city="深圳")  # 29-41K
        self.assertEqual(d.action, "REJECT")

    def test_under_30k_allow(self):
        d = evaluate_job("某司", "AI应用", "双休", "\ue032\ue035-\ue032\ue039K", city="深圳")  # 25-29K
        self.assertEqual(d.action, "ALLOW")

    def test_daily_salary_parsed_correctly(self):
        # 411-511元/天 → 9-11K/月（不是 411K！日薪区间解析回归）
        from job_decision import parse_salary_low, parse_salary_high
        self.assertAlmostEqual(parse_salary_low("\ue034\ue031\ue031-\ue035\ue031\ue031元/天"), 9.042, places=2)
        self.assertAlmostEqual(parse_salary_high("\ue034\ue031\ue031-\ue035\ue031\ue031元/天"), 11.242, places=2)

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
        # 2026-09-16 用户定稿：至少双休 → 大小周不论薪资一律拒（原「≥12K 可谈」特批已废除）
        d = evaluate_job("某科技", "AI应用工程师", "大小周", "15-20K")
        self.assertEqual(d.action, "REJECT")  # 大小周高薪同样死
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
    """2026-09-05 回归：Boss 图标字体 PUA 薪资字符必须能解码（薪资要读对才能判红线）。"""

    def test_pua_digits_decoded(self):
        from job_decision import parse_salary_low
        # \ue032\ue039 = "29"（0xe030=0, 0xe039=9）
        self.assertEqual(parse_salary_low("\ue032\ue039-\ue034\ue031K"), 29.0)
        self.assertEqual(parse_salary_low("\ue035\ue030-\ue031\ue030\ue030K"), 50.0)

    def test_pua_salary_daxiaozhou_always_reject(self):
        # 18-26K 大小周：薪资要能正确解码，但制度红线不看薪资 → 一律拒
        from job_decision import parse_salary_low
        self.assertEqual(parse_salary_low("\ue031\ue038-\ue032\ue036K"), 18.0)
        d = evaluate_job("优必选", "AI专家", "大小周", "\ue031\ue038-\ue032\ue036K", city="深圳")
        self.assertEqual(d.action, "REJECT")

    def test_pua_low_salary_daxiaozhou_still_reject(self):
        # 4-6K 大小周 → 同样拒
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


class TestAnnualSalaryParsing(unittest.TestCase):
    """2026-09-10 修复: 年薪格式(万/年)曾被当月薪万误判为150K→误拦30K线。

    51job 上"15-22万/年"很常见,误拦会漏掉大量可投岗(AI解决方案经理类)。
    """

    def test_annual_salary_converted_to_monthly(self):
        from job_decision import parse_salary_low, parse_salary_high
        # 15万/年 = 12.5K/月
        self.assertAlmostEqual(parse_salary_low("15-22万/年"), 12.5, places=1)
        self.assertAlmostEqual(parse_salary_high("15-22万/年"), 18.3, places=1)

    def test_annual_salary_within_30k_allowed(self):
        from job_decision import evaluate_job
        d = evaluate_job("某公司", "AI解决方案经理", "", "15-30万/年", city="深圳")
        self.assertEqual(d.action, "ALLOW")

    def test_high_annual_salary_still_rejected(self):
        from job_decision import evaluate_job
        # 80-100万/年 = 66.7-83.3K/月 → 超30K线,应拦
        d = evaluate_job("某公司", "AI智能化专家", "", "80-100万/年", city="深圳")
        self.assertEqual(d.action, "REJECT")

    def test_monthly_wan_unaffected(self):
        from job_decision import parse_salary_low
        # "15-22万"(无/年) = 月薪万单位 → 150K,保持原行为
        self.assertAlmostEqual(parse_salary_low("15-22万"), 150.0, places=1)


class TestDisabilityJobRejection(unittest.TestCase):
    """2026-09-10: 助残岗(残疾人专项)误投案例,加入排除词。"""

    def test_disability_keywords_in_config(self):
        import json
        from pathlib import Path as _P
        cfg = json.load(open(_P(__file__).resolve().parent.parent / 'config.json'))
        excl = cfg["exclude_keywords"]
        for w in ["助残", "残疾人"]:
            self.assertIn(w, excl)


class TestPaySuffixSalaryParsing(unittest.TestCase):
    """2026-09-12 修复: 「·13薪/·14薪」后缀致薪资解析失败 → 30K 红线永久失效。

    根因: _parse_salary_value 用 float(s.replace("万","")) 解析,
    "5万·13薪" → "5·13薪" → ValueError → 返回 0.0 → high=0 →
    `high > 30` 永不成立。51job/Boss 上「·13薪」极常见,属系统性漏投。
    实测漏投: 安帝爱科技 2.5-5万·13薪(50K)、广州美聚优选 2-4万·13薪(40K)。
    """

    # ── 正例: 带薪后缀且上界 >30K,必须触发红线 ──
    def test_suffix_high_range_rejected(self):
        from job_decision import evaluate_job
        d = evaluate_job("安帝爱科技（深圳）有限公司", "软件工程师", "", "2.5-5万·13薪", city="深圳")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("30K", d.reason)

    def test_suffix_high_range_rejected_2(self):
        from job_decision import evaluate_job
        d = evaluate_job("广州美聚优选影视传媒有限公司", "AI应用工程师", "", "2-4万·13薪", city="广州")
        self.assertEqual(d.action, "REJECT")

    # ── 反例: 带同样后缀但上界 ≤30K,必须放行(不能因后缀一律拦) ──
    def test_suffix_within_cap_allowed(self):
        from job_decision import evaluate_job
        d = evaluate_job("深圳市米尔电子有限公司", "AI Agent应用工程师", "", "1.6-2.4万·13薪", city="深圳")
        self.assertEqual(d.action, "ALLOW")

    # ── 边界: 上界正好等于 30K → 不拦(红线是 >30K) ──
    def test_suffix_boundary_exactly_30k_allowed(self):
        from job_decision import evaluate_job, parse_salary_high
        self.assertAlmostEqual(parse_salary_high("1.5-3万·14薪"), 30.0, places=1)
        d = evaluate_job("某公司", "AI应用工程师", "", "1.5-3万·14薪", city="深圳")
        self.assertEqual(d.action, "ALLOW")

    # ── 单值 + 后缀: 原实现 low/high 双双为 0,现必须都能解析 ──
    def test_single_value_with_suffix(self):
        from job_decision import parse_salary_low, parse_salary_high
        self.assertAlmostEqual(parse_salary_low("2.5万·13薪"), 25.0, places=1)
        self.assertAlmostEqual(parse_salary_high("2.5万·13薪"), 25.0, places=1)

    def test_single_value_with_suffix_over_30k_rejected(self):
        from job_decision import evaluate_job
        d = evaluate_job("某公司", "AI专家", "", "40万·13薪", city="深圳")
        self.assertEqual(d.action, "REJECT")

    # ── 回归: 无后缀 / 其他单位格式不受影响 ──
    def test_no_suffix_regression(self):
        from job_decision import parse_salary_low, parse_salary_high
        self.assertAlmostEqual(parse_salary_low("2.5-4万"), 25.0, places=1)
        self.assertAlmostEqual(parse_salary_high("2.5-4万"), 40.0, places=1)

    def test_k_format_regression(self):
        from job_decision import parse_salary_high
        self.assertAlmostEqual(parse_salary_high("30-50K"), 50.0, places=1)

    def test_annual_and_daily_regression(self):
        from job_decision import parse_salary_low, parse_salary_high
        self.assertAlmostEqual(parse_salary_low("15-22万/年"), 12.5, places=1)
        self.assertAlmostEqual(parse_salary_low("411-511元/天"), 9.04, places=2)
        self.assertAlmostEqual(parse_salary_high("411-511元/天"), 11.24, places=2)

    def test_unknown_salary_unaffected(self):
        from job_decision import parse_salary_high
        self.assertEqual(parse_salary_high("面议"), 0.0)

    def test_shared_lower_bound_with_suffix(self):
        """shared.parse_salary_lower_bound 也是同款 replace("万","") 写法，
        单值+后缀场景原返回 None（被当「薪资未知」）。"""
        from shared import parse_salary_lower_bound as f
        self.assertEqual(f("2.5万·13薪"), 25)
        self.assertEqual(f("5万·13薪"), 50)
        self.assertEqual(f("2.5-5万·13薪"), 25)
        self.assertEqual(f("15-25K·13薪"), 15)
        self.assertIsNone(f("面议"))

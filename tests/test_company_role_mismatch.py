#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主体-岗位错配规则（Priority 7.5）单元测试（2026-10-19 加固配套）。

背景：51job/猎聘的 desc 字段常为空串，正文层扫描（scam_guard 的 L1）在这两个
平台完全不生效。Priority 7.5 只用 company × title 判定，专治这个盲区：
咨询/人才服务/实训基地主体挂牌招「技术开发岗」= 招转培/卖课前台高发特征。

边界语义（来自 scam-defense-layers 文档，勿回退）：
  - 只拦「错配」组合：咨询主体招销售岗不套用本条，其余规则照跑；
  - 正常科技公司不受影响；
  - 命中返回 (0, "主体岗位错配:…")，未命中原样返回入参 score。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deep_filter import (  # noqa: E402
    DECISION_PRIORITY,
    MISMATCH_COMPANY_WORDS,
    TECH_ROLE_WORDS,
    deep_filter,
    is_filtered,
)


def run(company, title, score=77, desc="", salary="15-25K"):
    return deep_filter(company, title, desc, salary, score)


class TestCompanyRoleMismatchHit(unittest.TestCase):
    """错配组合必须拦，且原因可读。"""

    def test_consulting_company_tech_role_blocked(self):
        score, reason = run("远大管理咨询有限公司", "后端开发工程师")
        self.assertTrue(is_filtered(score, reason))
        self.assertIn("主体岗位错配", reason)
        self.assertIn("管理咨询", reason)

    def test_case_insensitive_ai_in_title(self):
        # TECH_ROLE_WORDS 里的 "ai" 依赖小写化匹配：标题写大写 AI 也要命中
        score, reason = run("启航教育咨询", "AI 训练师")
        self.assertTrue(is_filtered(score, reason))
        self.assertIn("主体岗位错配", reason)

    def test_works_with_empty_desc(self):
        # 51job/猎聘 场景：desc 为空串也必须拦（本规则不依赖正文）
        score, reason = run("某某人才服务有限公司", "数据开发工程师", desc="")
        self.assertTrue(is_filtered(score, reason))

    def test_every_mismatch_company_word_is_covered(self):
        for kw in MISMATCH_COMPANY_WORDS:
            score, reason = run(f"示例{kw}有限公司", "算法工程师")
            self.assertTrue(is_filtered(score, reason), f"{kw} 未命中")

    def test_every_tech_role_word_is_covered(self):
        for kw in TECH_ROLE_WORDS:
            score, reason = run("示例实训基地", f"招聘{kw}专员")
            self.assertTrue(is_filtered(score, reason), f"技术词「{kw}」未命中")


class TestCompanyRoleMismatchPass(unittest.TestCase):
    """非错配组合必须放行（原样返回入参 score），防止误杀。"""

    def test_consulting_company_sales_role_passes(self):
        # 咨询主体招销售岗：其余规则照跑，本条不拦
        score, reason = run("远大管理咨询有限公司", "销售代表")
        self.assertFalse(is_filtered(score, reason))
        self.assertEqual(score, 77)
        self.assertEqual(reason, "")

    def test_normal_tech_company_tech_role_passes(self):
        score, reason = run("云杉网络科技有限公司", "后端开发工程师")
        self.assertFalse(is_filtered(score, reason))
        self.assertEqual(score, 77)

    def test_partial_word_consulting_not_blocked(self):
        # 公司名只含「咨询」两个字（不在 MISMATCH_COMPANY_WORDS 表内）不触发本条
        score, reason = run("云咨询科技", "后端开发工程师")
        self.assertFalse(is_filtered(score, reason))

    def test_empty_company_never_blocked(self):
        score, reason = run("", "后端开发工程师")
        self.assertFalse(is_filtered(score, reason))

    def test_passthrough_score_value_preserved(self):
        # 未命中规则时返回入参 score（此处故意用非 0 值锁定「透传」语义）
        score, _ = run("正常科技", "测试工程师", score=63)
        self.assertEqual(score, 63)


class TestRuleOrdering(unittest.TestCase):
    """优先级链行为锁定。"""

    def test_priority_list_contains_rule_before_preserve(self):
        self.assertIn("company_role_mismatch", DECISION_PRIORITY)
        self.assertGreater(
            DECISION_PRIORITY.index("preserve"),
            DECISION_PRIORITY.index("company_role_mismatch"),
        )

    def test_company_name_risk_wins_over_mismatch(self):
        # 公司名同时含人力词（Priority 7）与错配词（7.5）时，Priority 7 先截断
        score, reason = run("某某人力资源管理有限公司", "后端开发工程师")
        self.assertTrue(is_filtered(score, reason))
        self.assertIn("人力中介", reason)
        self.assertNotIn("主体岗位错配", reason)

    def test_tech_role_words_nonempty(self):
        self.assertTrue(MISMATCH_COMPANY_WORDS)
        self.assertTrue(TECH_ROLE_WORDS)


if __name__ == "__main__":
    unittest.main()

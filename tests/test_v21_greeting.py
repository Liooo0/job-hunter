#!/usr/bin/env python3
"""v2.1 任务三：招呼语模板版本埋点单元测试。

覆盖：
- 模板 id 确定性：同一公司重跑必得同一模板（md5 轮换，不受 PYTHONHASHSEED 影响）；
- 三风格变体齐全（T1/T2/T3），同角色不同风格产出不同文案；
- generate_greeting 兼容包装行为不变；
- 素材约束：bg 来自 USER_BG 真实背景、问句来自角色词表，不编造经历。
"""
import hashlib
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from boss_apply import (  # noqa: E402
    GREETING_STYLES,
    GREETING_STYLE_ORDER,
    ROLE_QUESTIONS,
    USER_BG,
    generate_greeting,
    generate_greeting_with_meta,
    pick_greeting_style,
)


class TestStyleDeterminism(unittest.TestCase):
    def test_three_styles_defined(self):
        self.assertEqual(tuple(GREETING_STYLES.keys()), ("T1", "T2", "T3"))
        self.assertEqual(GREETING_STYLE_ORDER, ("T1", "T2", "T3"))

    def test_pick_style_matches_md5_formula(self):
        for company in ("腾讯", "比亚迪", "", "Acme Corp"):
            expect = GREETING_STYLE_ORDER[
                int(hashlib.md5(company.encode("utf-8")).hexdigest(), 16) % 3]
            self.assertEqual(pick_greeting_style(company), expect)

    def test_same_company_same_style_across_calls(self):
        seen = {pick_greeting_style("某科技有限公司") for _ in range(20)}
        self.assertEqual(len(seen), 1)

    def test_all_three_styles_reachable(self):
        styles = {pick_greeting_style(f"公司{i}号") for i in range(50)}
        self.assertEqual(styles, {"T1", "T2", "T3"})

    def test_template_id_format_and_stability(self):
        g1, tid1 = generate_greeting_with_meta("AI应用工程师", "负责大模型应用落地",
                                               "字节跳动")
        g2, tid2 = generate_greeting_with_meta("AI应用工程师", "负责大模型应用落地",
                                               "字节跳动")
        self.assertEqual((g1, tid1), (g2, tid2))  # 同岗位重跑结果一致
        style, role = tid1.split(":", 1)
        self.assertIn(style, GREETING_STYLE_ORDER)
        self.assertIn(role, ROLE_QUESTIONS)


class TestGreetingContent(unittest.TestCase):
    def test_role_matching_unchanged(self):
        _, tid = generate_greeting_with_meta("Python后端工程师", "熟悉Django",
                                             "测试A")
        self.assertTrue(tid.endswith(":Python"))
        # 采购人设优先规则保留
        _, tid = generate_greeting_with_meta("采购工程师", "负责IT设备采购",
                                             "测试B")
        self.assertTrue(tid.endswith(":采购"))

    def test_bg_from_user_background_no_fabrication(self):
        greeting, _ = generate_greeting_with_meta(
            "Python开发", "会python脚本", "测试C")
        self.assertIn(USER_BG["Python"], greeting)  # bg 一字不改来自真实背景
        q = ROLE_QUESTIONS["Python"]
        self.assertIn(q, greeting)

    def test_default_role_fallback(self):
        greeting, tid = generate_greeting_with_meta("行政专员", "整理文档",
                                                    "测试D")
        self.assertTrue(tid.endswith(":默认"))
        self.assertIn(USER_BG["默认"], greeting)

    def test_styles_produce_different_text_for_same_role(self):
        texts = set()
        for style in GREETING_STYLE_ORDER:
            pattern = GREETING_STYLES[style]["pattern"]
            texts.add(pattern.format(title="测试工程师", bg=USER_BG["测试"],
                                     q=ROLE_QUESTIONS["测试"]))
        self.assertEqual(len(texts), 3)  # 三种风格互不相同

    def test_length_cap(self):
        greeting, _ = generate_greeting_with_meta(
            "一个特别特别特别长的岗位名称超过二十个字的处理", "x" * 200, "测试E")
        self.assertLessEqual(len(greeting), 120)

    def test_compat_wrapper_returns_text_only(self):
        text = generate_greeting("Agent工程师", "做智能体", "测试F")
        meta_text, _ = generate_greeting_with_meta("Agent工程师", "做智能体",
                                                   "测试F")
        self.assertEqual(text, meta_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

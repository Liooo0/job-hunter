#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5.2 send-link test: chat-page company stem matching.
Reflects boss_apply._send_greeting_via_chat inline logic (2026-08-31)."""
import unittest
from collections import Counter

_GENERIC = {"中国", "中华", "有限", "公司", "集团", "科技", "技术", "网络",
            "信息", "电子", "咨询", "服务", "深圳", "广州", "上海", "北京",
            "杭州", "南京", "成都", "天津", "数据", "智能", "人工"}


def company_stems(company, maxlen=8):
    search = (company or "")[:maxlen]
    if len(search) <= 2:
        return [search] if search else []
    grams = Counter(search[i:i+2] for i in range(len(search)-1))
    return [g for g, _ in sorted(grams.items(), key=lambda kv: (-kv[1], search.index(kv[0])))
            if g not in _GENERIC]


def match(conv_text, company):
    """复刻 JS 逻辑：词干唯一命中 或 整名互contain。"""
    for s in company_stems(company):
        if s in conv_text:
            return True
    full = (company or "")[:8]
    t = conv_text.strip()
    return bool(t) and len(t) >= 2 and (full in t or t in full)


class TestCompanyStem(unittest.TestCase):
    def test_huawei(self):
        self.assertTrue(match("华为hr", "华为技术有限公司"))

    def test_pingan_variant(self):
        self.assertTrue(match("平安人寿", "中国平安"))

    def test_taobao_shangou(self):
        self.assertTrue(match("淘天集团-淘宝闪购", "淘宝闪购"))

    def test_short_name(self):
        self.assertTrue(match("中孚信息股份有限公司", "中孚信息"))

    def test_generic_blacklist_no_wrong_hit(self):
        # "中国" 是黑名单词，不能靠它命中；"中国平安" 的 "平安" 词干才是真信号
        self.assertNotIn("中国", company_stems("中国平安"))
        self.assertIn("平安", company_stems("中国平安"))

    def test_no_false_positive(self):
        self.assertFalse(match("腾讯科技", "华为技术有限公司"))

    def test_stem_priority_company_specific(self):
        # "华为技术有限公司" 词干优先出"华为"（非黑名单里最前位置）
        stems = company_stems("华为技术有限公司")
        self.assertEqual(stems[0], "华为")


if __name__ == "__main__":
    unittest.main()

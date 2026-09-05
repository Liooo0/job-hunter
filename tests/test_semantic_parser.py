#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5.2 Semantic Parser 黄金回归集（2026-08-31）。

第一批黄金案例 = 真实漏网的两条（用户定稿：它们永远不能再漏出去）：
    "AI+客户服务顾问｜平安康养财富｜金融顾问"  → 实际金融销售
    "AI 影视视频评测"                        → 实际数据标注

同时覆盖：
    正例（合法目标岗必须放行，防误杀）
    边界案例（歧义词、豁免序列）
    Schema 校验（坏数据必须被拦成 UNKNOWN，绝不放行）
"""
import unittest

import semantic_parser as SP


class TestGoldenRegressions(unittest.TestCase):
    """真实漏网案例 —— 永不复漏。"""

    def test_pingan_financial_sales_disguise(self):
        # 实测漏网：标题看起来像"AI+顾问"，实际是金融销售
        out = SP.parse("AI+客户服务顾问｜平安康养财富｜金融顾问")
        self.assertEqual(out["verdict"], "HARD_BLOCK")
        self.assertTrue(out["sales"])
        self.assertFalse(out["ai_core"])
        self.assertEqual(SP.gate("AI+客户服务顾问｜平安康养财富｜金融顾问"),
                         "实际为销售/金融顾问岗（标题或JD证据），非目标序列")

    def test_ai_video_evaluation_is_annotation(self):
        # 实测漏网：标题带"AI"和"评测"，实际是数据标注流水线
        out = SP.parse("AI 影视视频评测")
        self.assertEqual(out["verdict"], "HARD_BLOCK")
        self.assertTrue(out["annotation"])
        self.assertEqual(SP.gate("AI 影视视频评测"),
                         "实际为数据标注/评测流水线岗，非目标序列")

    def test_jd_sales_signals_block_even_with_clean_title(self):
        # 标题干净但 JD 全是销售动作 → 照样拦
        jd = "负责客户开发，完成业绩指标，推动产品推广，实现客户转化"
        out = SP.parse("AI 解决方案顾问", jd_text=jd)
        self.assertEqual(out["verdict"], "HARD_BLOCK")
        self.assertTrue(out["sales"])


class TestLegitimateJobsPass(unittest.TestCase):
    """合法目标岗必须放行 —— 防语义层误杀真机会。"""

    def test_ai_application_engineer(self):
        self.assertIsNone(SP.gate("AI应用工程师", "负责RAG知识库与工作流落地"))

    def test_ai_delivery_engineer(self):
        self.assertIsNone(SP.gate("AI实施工程师", "大模型应用部署与交付"))

    def test_ai_solution_consultant_clean(self):
        # "AI解决方案顾问"本身合法（无销售证据）→ 放行，交给 L2 资格层
        self.assertIsNone(SP.gate("AI解决方案顾问"))


class TestEdgeCases(unittest.TestCase):
    """边界案例：豁免序列、单弱信号、未知。"""

    def test_llm_evaluation_engineer_not_killed_by_annotation(self):
        # "大模型评测工程师"属测试序列，应豁免而非被标注词秒杀
        out = SP.parse("大模型评测工程师")
        self.assertNotEqual(out["verdict"], "HARD_BLOCK",
                            "评测工程师属测试序列，语义层不应秒杀，交给L2资格层")

    def test_unknown_title_stays_unknown_not_blocked(self):
        # 语义层只对识别出的伪装下死手，识别不了 → UNKNOWN 放行给 L2
        out = SP.parse("运营专员")
        self.assertEqual(out["verdict"], "UNKNOWN")
        self.assertIsNone(SP.gate("运营专员"))

    def test_deterministic_same_input_same_output(self):
        a = SP.parse("AI+客户服务顾问｜平安康养财富｜金融顾问")
        b = SP.parse("AI+客户服务顾问｜平安康养财富｜金融顾问")
        self.assertEqual(a, b, "确定性层必须同输入同输出")


class TestSchemaGuard(unittest.TestCase):
    """R-EXT-3：坏数据必须被拦成 UNKNOWN，绝不放行。"""

    def test_valid_output_passes_schema(self):
        out = SP.parse("AI应用工程师")
        self.assertTrue(SP.validate_parse_output(out))

    def test_malformed_output_rejected(self):
        bad = {"category": "我觉得挺合适", "verdict": "maybe"}
        self.assertFalse(SP.validate_parse_output(bad))

    def test_guard_downgrades_bad_data_to_unknown(self):
        # 直接喂坏源（构造异常路径）→ 闸门返回 UNKNOWN，不抛、不放行
        out = SP.parse_with_schema_guard("AI应用工程师")
        self.assertIn(out["category"], SP.CATEGORY_ENUM)
        self.assertIn(out["verdict"], {"PASS", "HARD_BLOCK", "UNKNOWN"})

    def test_evidence_present_for_every_block(self):
        # R-EXT-4：每个 HARD_BLOCK 必须带原文证据（面试可辩护同源）
        for title in ["AI+客户服务顾问｜平安康养财富｜金融顾问", "AI 影视视频评测"]:
            out = SP.parse(title)
            self.assertEqual(out["verdict"], "HARD_BLOCK")
            self.assertTrue(out["evidence"], f"拦截必须给出证据: {title}")


class TestGoldenThreeClassFile(unittest.TestCase):
    """黄金集三类文件 tests/golden_cases.json 驱动 — 分类库不是死文档。

    规则：NEGATIVE 不漏 / POSITIVE 不杀 / BOUNDARY 不乱（=语义层不直接否决，非 PASS）。
    """
    import json as _json
    from pathlib import Path as _P

    def setUp(self):
        p = self._P(__file__).parent / "golden_cases.json"
        data = self._json.loads(p.read_text(encoding="utf-8"))
        self.pos = data["GOLDEN_POSITIVE"]["cases"]
        self.neg = data["GOLDEN_NEGATIVE"]["cases"]
        self.bnd = data["GOLDEN_BOUNDARY"]["cases"]

    def test_negative_never_escapes(self):
        for c in self.neg:
            out = SP.parse(c["title"], c.get("jd", ""))
            self.assertEqual(out["verdict"], "HARD_BLOCK",
                             f"NEGATIVE 漏网: {c['title']}")

    def test_positive_never_killed_by_semantic(self):
        for c in self.pos:
            self.assertIsNone(SP.gate(c["title"], c.get("jd", "")),
                              f"POSITIVE 被语义层误杀: {c['title']}")

    def test_boundary_not_directly_vetoed(self):
        for c in self.bnd:
            out = SP.parse(c["title"], c.get("jd", ""))
            self.assertNotEqual(out["verdict"], "HARD_BLOCK",
                                f"BOUNDARY 被语义层秒杀(应交给L2): {c['title']}")


if __name__ == "__main__":
    unittest.main()


class TestTechRoleNoKill(unittest.TestCase):
    """2026-09-05 误杀回归：技术岗 JD 含客户/推广词不得被当销售拦。"""

    def test_huawei_ai_engineer_not_sales(self):
        out = SP.parse("AI软件工程师", "负责AI软件开发，与客户沟通需求，推动产品推广", "华为")
        self.assertNotEqual(out["verdict"], "HARD_BLOCK")

    def test_ai_product_tech_manager_not_sales(self):
        out = SP.parse("AI产品技术经理", "负责技术方案，对接客户需求，电话沟通", "庭宇")
        self.assertNotEqual(out["verdict"], "HARD_BLOCK")

    def test_real_sales_title_still_blocked(self):
        out = SP.parse("客户经理", "负责客户开发，产品推广，电话沟通，陌拜")
        self.assertEqual(out["verdict"], "HARD_BLOCK")

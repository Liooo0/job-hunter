#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RULES_v2.0 第三层：岗位价值评分器回归测试 —— 只排序不拦截。

核心语义（用户定稿，勿回退）：
- 12K AI应用 双休 > 8.5K AI应用 双休 > 南方电网5-7K编制（稳定换薪资）
- 8.5K AI应用 双休 五险一金 > 12K 外包驻场（AI匹配度拉开差距）
- 客服 8-10K 双休 → LOW；标注 <5K → 资格层拒，评分器不背锅
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from job_decision import evaluate_job
from match_engine import explain_match, load_candidate_profile
from value_score import value_score

_CFG = {}
_p = Path(__file__).resolve().parent.parent / "config.json"
if _p.exists():
    _CFG = json.loads(_p.read_text())


def score_of(company, title, desc, salary, city="深圳"):
    dec = evaluate_job(company, title, desc, salary, city=city, cfg=_CFG)
    self = None
    mr = explain_match(title, desc, company=company, salary=salary, city=city, cfg=_CFG)
    vs = value_score(company, title, desc, salary, city=city,
                     decision=dec, match_result=mr, salary_band=dec.salary_band)
    return vs, dec


class TestValueScoreBasics(unittest.TestCase):
    def test_high_sal_ai_high_priority(self):
        vs, dec = score_of("某科技", "AI应用工程师", "RAG知识库,Agent工作流,双休,五险一金", "12-20K")
        self.assertEqual(dec.action, "ALLOW")
        self.assertEqual(vs.tier, "HIGH")
        self.assertGreaterEqual(vs.score, 80)

    def test_mid_sal_ai_normal_or_high(self):
        vs, dec = score_of("某科技", "AI应用工程师", "RAG知识库开发,双休,五险一金", "8.5-12K")
        self.assertEqual(dec.action, "ALLOW")
        self.assertIn(vs.tier, ("HIGH", "NORMAL"))

    def test_mid_sal_admin_low(self):
        vs, dec = score_of("某公司", "客服专员", "客户服务", "8-10K")
        self.assertEqual(dec.action, "ALLOW")       # 资格层放行（8-10K 正常档）
        self.assertEqual(vs.tier, "LOW")            # 但价值排序垫底

    def test_under_5k_rejected_at_gate(self):
        vs, dec = score_of("某公司", "数据标注员", "纯标注作业", "4-6K")
        self.assertEqual(dec.action, "REJECT")      # 资格层拦截，根本没到排序

    def test_unknown_salary_not_rejected(self):
        vs, dec = score_of("某Agent科技", "Agent应用工程师", "Agent工作流开发,双休", "面议")
        self.assertEqual(dec.action, "ALLOW")
        self.assertEqual(dec.salary_band, "unknown")
        self.assertGreaterEqual(vs.score, 60)


class TestValueScoreOrdering(unittest.TestCase):
    """用户定稿的排序语义（最核心，勿回退）。"""

    def test_12k_ai_beats_8k_ai(self):
        hi, _ = score_of("某科技", "AI应用工程师", "RAG知识库,双休,五险一金", "12-20K")
        lo, _ = score_of("某科技", "AI应用工程师", "RAG知识库,双休,五险一金", "8.5-12K")
        self.assertGreater(hi.score, lo.score)

    def test_8k_ai_beats_south_grid_5k(self):
        ai, _ = score_of("某科技", "AI应用工程师", "RAG知识库,双休,五险一金", "8.5-12K")
        grid, _ = score_of("南方电网", "数据运维值班员", "正式编制,双休,五险一金齐全", "5-7K")
        self.assertGreater(ai.score, grid.score)     # 8.5K AI 双休 > 5-7K 编制

    def test_12k_ai_beats_12k_outsourcing(self):
        ai, _ = score_of("某科技", "AI应用工程师", "RAG知识库,双休,五险一金", "12-20K")
        out, _ = score_of("某外包", "测试驻场", "华为驻场", "12-16K")
        self.assertGreater(ai.score, out.score)      # 同薪，AI匹配度拉开

    def test_south_grid_stability_rewarded(self):
        grid, _ = score_of("南方电网", "数据运维值班员", "正式编制,双休,五险一金齐全", "5-7K")
        admin, _ = score_of("某私企", "行政助理", "双休", "6-8K")
        self.assertGreaterEqual(grid.score, admin.score)  # 编制稳定分应体现


if __name__ == "__main__":
    unittest.main()
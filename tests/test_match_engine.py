#!/usr/bin/env python3
"""match_engine 单元测试（P0-5）。

跑法一（unittest discover，从项目根目录）:
    PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -t . -v

跑法二（直接运行）:
    cd tests && PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 test_match_engine.py
"""
import json
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from match_engine import (  # noqa: E402
    _aggregate,
    contains_kw,
    explain_match,
    format_report,
    load_candidate_profile,
    verdict_for,
)
from shared import load_config  # noqa: E402


def minimal_cfg():
    """与真实 config 同构的最小配置（无 target_roles，模拟线上缺失态）。"""
    return {
        "skills": ["Python", "RAG", "Dify", "知识库"],
        "boost_keywords": ["Agent", "工作流", "大模型"],
        "exclude_keywords": [],
        "must_contain": [],
        "target_roles": [],
        "job_pools": {"keywords": {
            "S级-AI应用工程师": ["AI应用工程师", "RAG", "Dify"],
            "A级-车联网/智能汽车": ["车载测试"],
            "B级-AI内容运营": ["内容运营"],
        }},
        "salary_filter": {"home_cities": ["深圳"]},
        "city_pools": {"city_priority": {"深圳": "primary", "广州": "secondary",
                                         "上海": "opportunistic"}},
        "min_score": 60,
    }


class TestContainsKW(unittest.TestCase):
    def test_ascii_false_positives_fixed(self):
        self.assertFalse(contains_kw("Maintained legacy systems", "ai"))
        self.assertFalse(contains_kw("Google 招聘", "go"))
        self.assertFalse(contains_kw("RapidAPI 平台", "api"))

    def test_case_insensitive_and_boundary_hit(self):
        self.assertTrue(contains_kw("AI应用工程师", "ai"))
        self.assertTrue(contains_kw("熟悉Python", "python"))
        self.assertTrue(contains_kw("会 go 语言", "Go"))

    def test_symbol_side_no_boundary(self):
        self.assertTrue(contains_kw("要求 .NET 经验", ".net"))
        self.assertTrue(contains_kw("C++开发", "c++"))
        self.assertFalse(contains_kw("用 C 加加", "c++"))

    def test_chinese_substring(self):
        self.assertTrue(contains_kw("负责知识库建设", "知识库"))
        self.assertTrue(contains_kw("知识库", "知识库"))
        self.assertFalse(contains_kw("", "知识库"))
        self.assertFalse(contains_kw("正文", ""))


class TestDimensions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def test_s_tier_title_direction(self):
        r = explain_match("AI应用工程师", "负责大模型应用开发", cfg=self.cfg)
        self.assertGreaterEqual(r["dimensions"]["direction"]["score"], 85)

    def test_target_roles_priority(self):
        cfg = dict(minimal_cfg())
        cfg["target_roles"] = ["AI应用工程师"]
        r = explain_match("AI应用工程师", "开发", cfg=cfg)
        self.assertEqual(r["dimensions"]["direction"]["score"], 95)

    def test_traditional_stack_penalty(self):
        trad_desc = "负责Android App和Flutter开发，使用Java和Spring框架，前端Vue"
        clean = explain_match("软件开发工程师", trad_desc, cfg=self.cfg)
        protected = explain_match("软件开发工程师", trad_desc + "，同时用Python做大模型Agent应用",
                                  cfg=self.cfg)
        self.assertLess(clean["dimensions"]["technical"]["score"],
                        protected["dimensions"]["technical"]["score"])
        self.assertLessEqual(clean["dimensions"]["technical"]["score"], 40)

    def test_shenzhen_primary_location(self):
        r = explain_match("AI工程师", "开发", city="深圳", cfg=minimal_cfg())
        self.assertEqual(r["dimensions"]["location"]["score"], 90)

    def test_remote_location(self):
        r = explain_match("AI工程师", "可远程办公", city="北京", cfg=minimal_cfg())
        self.assertEqual(r["dimensions"]["location"]["score"], 95)

    def test_city_tiers(self):
        base = dict(minimal_cfg())  # home_cities 只有深圳 → 广州走 secondary 档
        for city, expect in (("广州", 70), ("上海", 55), ("乌鲁木齐", 50)):
            r = explain_match("AI工程师", "开发", city=city, cfg=base)
            self.assertEqual(r["dimensions"]["location"]["score"], expect, city)
        cfg2 = dict(minimal_cfg())
        cfg2["salary_filter"] = {"home_cities": ["深圳", "珠海"]}
        r = explain_match("AI工程师", "开发", city="珠海", cfg=cfg2)
        self.assertEqual(r["dimensions"]["location"]["score"], 80)

    def test_culture_outsourcing_and_996(self):
        cfg = minimal_cfg()
        r1 = explain_match("AI工程师", "正常作息", company="中软国际", cfg=cfg)
        self.assertLess(r1["dimensions"]["culture"]["score"], 75)
        r2 = explain_match("AI工程师", "公司实行996工作制", cfg=cfg)
        self.assertLessEqual(r2["dimensions"]["culture"]["score"], 45)

    def test_experience_tiers(self):
        cfg = minimal_cfg()
        self.assertEqual(
            explain_match("AI工程师", "经验不限", cfg=cfg)["dimensions"]["experience"]["score"], 85)
        self.assertEqual(
            explain_match("AI工程师", "要求3年以上经验", cfg=cfg)["dimensions"]["experience"]["score"], 35)
        self.assertEqual(
            explain_match("AI工程师", "团队合作", cfg=cfg)["dimensions"]["experience"]["score"], 70)


class TestAggregationAndVerdict(unittest.TestCase):
    def _dims_from_weighted(self, values):
        dims = {}
        weights = {"technical": 0.30, "direction": 0.30, "experience": 0.15,
                   "culture": 0.15, "location": 0.10}
        for name in weights:
            score = 100 if name == "location" else 0
            weighted = round(values[name] * 1.0, 1)
            dims[name] = {"score": score, "weight": weights[name],
                          "weighted": weighted, "evidence": []}
        return dims

    def test_total_equals_rounded_sum_of_weighted(self):
        cfg = load_config()
        r = explain_match("AI应用工程师", "RAG 知识库 Python", salary="15-25K",
                          city="深圳", cfg=cfg)
        s = sum(d["weighted"] for d in r["dimensions"].values())
        self.assertEqual(r["total"], int(s + 0.5))

    def test_verdict_boundaries(self):
        self.assertEqual(verdict_for(75), "strong_apply")
        self.assertEqual(verdict_for(74), "apply")
        self.assertEqual(verdict_for(60), "apply")
        self.assertEqual(verdict_for(59), "consider")
        self.assertEqual(verdict_for(45), "consider")
        self.assertEqual(verdict_for(44), "skip")

    def test_rounding_74x_vs_75x(self):
        dims74 = self._dims_from_weighted({
            "technical": 22.4, "direction": 22.4, "experience": 11.2,
            "culture": 11.2, "location": 7.2})   # 合计 74.4 → 四舍五入 74
        total74, raw = _aggregate(dims74)
        self.assertEqual(total74, 74)
        self.assertEqual(verdict_for(total74), "apply")

        dims75 = self._dims_from_weighted({
            "technical": 22.6, "direction": 22.4, "experience": 11.2,
            "culture": 11.2, "location": 7.3})   # 合计 74.7 → 75 跨过阈值
        total75, _ = _aggregate(dims75)
        self.assertEqual(total75, 75)
        self.assertEqual(verdict_for(total75), "strong_apply")


class TestRobustness(unittest.TestCase):
    def test_real_config_without_target_roles(self):
        cfg = load_config()  # config.json 无 target_roles 字段（DEFAULT_CONFIG 补空列表）
        self.assertFalse(cfg.get("target_roles"))
        profile = load_candidate_profile(cfg)
        self.assertIn("兜底", profile["notes"])
        self.assertTrue(profile["pools"]["S级"])
        r = explain_match("AI实施工程师", "负责企业AI落地", cfg=cfg)
        self.assertIsInstance(r["total"], int)

    def test_empty_desc_and_fields(self):
        r = explain_match("测试岗", "", company="", salary="", cfg=minimal_cfg())
        self.assertIn("薪资未标注", r["risks"])
        self.assertTrue(0 <= r["total"] <= 100)
        format_report(r)  # 不抛异常即可

    def test_cfg_none_loads_real_config(self):
        r = explain_match("AI工程师", "Python 开发")
        self.assertIsInstance(r["total"], int)


class TestRegression19(unittest.TestCase):
    """资格层判 pass 的案例，引擎不得给出 skip —— 保证评估层与既有口径不打架。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()
        data = json.loads((PROJECT / "tests/regression_cases.json").read_text(encoding="utf-8"))
        cls.cases = data["cases"]

    def test_all_pass_cases_not_skip(self):
        checked = 0
        for c in self.cases:
            if c["expect"] != "pass":
                continue
            r = explain_match(c["title"], c["desc"], company=c["company"],
                              salary=c["salary"], city="深圳", cfg=self.cfg)
            self.assertGreater(r["total"], 0, f"{c['id']} total={r['total']}")
            self.assertNotEqual(r["verdict"], "skip",
                                f"{c['id']} verdict=skip | {format_report(r)}")
            checked += 1
        self.assertEqual(checked, 8)  # v4.0后: 25例中8例 expect=pass(新增2例 pass)


if __name__ == "__main__":
    unittest.main(verbosity=2)

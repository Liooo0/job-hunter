#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JOB-HUNTER v5.1 location tier tests (2026-08-31 user spec)."""
import unittest

from relocation import (city_tier, evaluate_location, order_cities_by_tier,
                        load_location_cfg, plan2_floor_for_city)
from value_score import value_score, WEIGHTS
from job_decision import evaluate_job


class TestLocationTier(unittest.TestCase):
    def setUp(self):
        self.cfg = load_location_cfg()

    def test_four_tiers(self):
        self.assertEqual(city_tier("深圳", self.cfg), "S")
        self.assertEqual(city_tier("广州", self.cfg), "A")
        for c in ("杭州", "南京", "成都"):
            self.assertEqual(city_tier(c, self.cfg), "B", c)
        self.assertEqual(city_tier("武汉", self.cfg), "C")
        self.assertEqual(city_tier("", self.cfg), "C")

    def test_base_scores(self):
        self.assertEqual(evaluate_location("深圳", "", self.cfg).score, 15)
        self.assertEqual(evaluate_location("广州", "", self.cfg).score, 13)
        self.assertEqual(evaluate_location("杭州", "", self.cfg).score, 8)
        self.assertEqual(evaluate_location("武汉", "", self.cfg).score, 3)

    def test_home_city_local_requirement_exempt(self):
        # 深圳岗位写"仅限深圳本地"：人就在深圳，满分不罚
        ev = evaluate_location("深圳", "仅限深圳本地", self.cfg)
        self.assertEqual(ev.score, 15)

    def test_remote_bonus_and_local_only_penalty(self):
        base = evaluate_location("杭州", "AI应用工程师", self.cfg)
        remote = evaluate_location("杭州", "接受优秀的异地候选人", self.cfg)
        local = evaluate_location("杭州", "仅限杭州本地", self.cfg)
        self.assertEqual(remote.score, min(base.score + 3, 15))
        self.assertEqual(local.score, max(0, base.score - 10))
        self.assertEqual(local.risk, "extreme")

    def test_local_prefer_soft_penalty(self):
        # "本地优先" 在 B/C 档只是减分（-2），不是拦截；S/A 母城圈不罚
        self.assertEqual(evaluate_location("杭州", "本地优先").score, 8 - 2)
        self.assertEqual(evaluate_location("深圳", "本地优先").score, 15)

    def test_cap_15(self):
        ev = evaluate_location("广州", "接受异地", self.cfg)
        self.assertLessEqual(ev.score, 15)

    def test_plan2_salary_floors(self):
        # Plan2 门槛随档位抬高：外地的不是不能投，是要更高薪资才值得耗额度
        self.assertEqual(plan2_floor_for_city("深圳", self.cfg), 10000)
        self.assertEqual(plan2_floor_for_city("广州", self.cfg), 10000)
        self.assertEqual(plan2_floor_for_city("杭州", self.cfg), 12000)
        self.assertEqual(plan2_floor_for_city("成都", self.cfg), 12000)
        self.assertEqual(plan2_floor_for_city("武汉", self.cfg), 14000)

    def test_order_cities(self):
        out = order_cities_by_tier(["杭州", "深圳", "南京", "广州", "武汉"], self.cfg)
        self.assertEqual(out, ["深圳", "广州", "杭州", "南京", "武汉"])


class TestValueScoreV11(unittest.TestCase):
    def test_weights_sum_100(self):
        self.assertEqual(sum(WEIGHTS.values()), 100)
        self.assertEqual(WEIGHTS["location"], 15)

    def _v(self, city, sal, desc="RAG知识库开发,五险一金"):
        return value_score("某科技", "AI应用工程师", desc, sal, city=city)

    def test_ranking_anchor(self):
        # 用户定稿示例：深圳10K > 杭州15K > 杭州10K
        sz10 = self._v("深圳", "10-12K").score
        hz15 = self._v("杭州", "15-18K").score
        hz10 = self._v("杭州", "10-12K").score
        self.assertGreater(sz10, hz15)
        self.assertGreater(hz15, hz10)

    def test_location_signal_moves_score(self):
        # 只变地区信号、其余描述相同 → 地区分必须真实传导到总分
        base = self._v("杭州", "10-12K").score
        remote = self._v("杭州", "10-12K", "RAG知识库开发,五险一金,接受异地候选人").score
        local = self._v("杭州", "10-12K", "RAG知识库开发,五险一金,仅限杭州本地").score
        self.assertGreater(remote, base)
        self.assertLess(local, base)


class TestLocationIsNotHardGate(unittest.TestCase):
    """用户定稿：地区不能变成绝对硬门槛——外地岗位不得因城市被 REJECT。"""

    def test_far_city_still_allowed(self):
        for city in ("杭州", "南京", "成都", "武汉", "哈尔滨"):
            d = evaluate_job("某公司", "AI应用工程师", "RAG开发,双休", "12-15K", city=city)
            self.assertEqual(d.action, "ALLOW", city)

    def test_local_only_not_rejected_by_l2(self):
        # "仅限杭州本地" 是评分层-10，不是 L2 红线
        d = evaluate_job("某公司", "AI应用工程师", "RAG开发,双休,仅限杭州本地", "12-15K",
                         city="杭州")
        self.assertEqual(d.action, "ALLOW")


if __name__ == "__main__":
    unittest.main(verbosity=2)

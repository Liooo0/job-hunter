#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5 地基验收测试：profile单一事实源 / Schema证据机制 / 额度调度plan序。
对应用户定稿的验收标准 1,3,4,5,6,7。"""
import os
import sqlite3
import tempfile
import unittest

import yaml

import job_schema as js
import job_decision as jd
import quota_scheduler as qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = yaml.safe_load(open(os.path.join(ROOT, "config", "candidate_profile.yaml"), encoding="utf-8"))


class TestProfileSingleSource(unittest.TestCase):
    """验收1地基：profile 数字与 L2 代码常量必须一致，漂移即红。"""

    def test_salary_tiers_match_l2_constants(self):
        s = PROFILE["salary"]
        self.assertEqual(s["target"], jd.SALARY_PRIORITY * 1000)
        self.assertEqual(s["acceptable"], jd.SALARY_NORMAL_FLOOR * 1000)
        self.assertEqual(s["exceptional_floor"], jd.SALARY_HARD_FLOOR * 1000)

    def test_work_blocked_superset_of_l2_redlines(self):
        blocked = set(PROFILE["work"]["blocked"])
        for red in jd.WORKDAY_REDLINES:
            self.assertIn(red, blocked, f"L2红线词 {red} 不在profile.blocked → 双源漂移")

    def test_special_signals_match_l2(self):
        prof = set(PROFILE["special_override"]["formal_stable_job"]["signals"])
        l2 = set(jd.SPECIAL_APPROVAL_SIGNALS)
        self.assertTrue(prof <= l2 or l2 <= prof, "特批词表与L2漂移")

    def test_plan1_covers_four_tiers(self):
        self.assertEqual(set(PROFILE["career"]["plan1"]), {"P1-A", "P1-B", "P1-C", "P1-D"})

    def test_quota_uncertain_not_counted(self):
        self.assertFalse(PROFILE["quota"]["uncertain_counts_as_sent"])


class TestSchemaEvidence(unittest.TestCase):
    """验收1/防脑补：无证据字段不得参与判决。"""

    def test_e0_salary_dropped(self):
        rec = js.JobRecord.from_dict({
            "salary": {"value": {"min": 20000}, "evidence": []},
            "raw_signals": {"salary_low": 3}})
        rec.enforce_evidence()
        sig = rec.to_signals()
        self.assertEqual(sig["salary_low"], 3)   # 无证据→回退旧信号，不采信脑补值

    def test_evidence_overrides_raw(self):
        rec = js.JobRecord.from_dict({
            "salary": {"value": {"min": 12000}, "evidence": ["12-15K·13薪"], "level": "E2"},
            "raw_signals": {"salary_low": 3}})
        rec.enforce_evidence()
        self.assertEqual(rec.to_signals()["salary_low"], 12.0)

    def test_model_cannot_output_decision(self):
        errs = js.validate_model_output({"category": "AI_APPLICATION", "action": "ALLOW"})
        self.assertTrue(any("越权" in e for e in errs))

    def test_category_enum_enforced(self):
        errs = js.validate_model_output({"category": "我想投"})
        self.assertTrue(any("越界" in e for e in errs))

    def test_unknown_category_fallback(self):
        rec = js.JobRecord.from_dict({"job_title": "x"})
        self.assertEqual(rec.category, js.UNKNOWN)


class TestQuotaScheduler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.sch = qs.QuotaScheduler(db_path=self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _force_low_cap(self):
        # 用 monkeypatch 把生效上限压小，方便测试
        qs._load_limits = lambda: (3, 50)

    def test_budget_is_min_of_quota_and_cap(self):
        qs._load_limits = lambda: (150, 50)
        st = qs.QuotaScheduler(db_path=self.tmp.name).state()
        self.assertEqual(st.budget, 50)   # 风控硬顶优先（验收6）

    def test_acquire_respects_budget(self):
        self._force_low_cap()
        sch = qs.QuotaScheduler(db_path=self.tmp.name)
        self.assertTrue(sch.acquire("j1", "P1-A"))
        self.assertTrue(sch.acquire("j2"))
        self.assertTrue(sch.acquire("j3"))
        self.assertFalse(sch.acquire("j4"))   # 预算3用满 → 拒（验收5）

    def test_uncertain_pending_then_verify_counts(self):
        self._force_low_cap()
        sch = qs.QuotaScheduler(db_path=self.tmp.name)
        sch.acquire("j1")
        st = sch.state()
        self.assertEqual(st.sent, 0)          # UNCERTAIN 不扣额（验收7）
        self.assertEqual(st.pending, 1)
        sch.verify("j1")
        self.assertEqual(sch.state().sent, 1)

    def test_release_refunds(self):
        self._force_low_cap()
        sch = qs.QuotaScheduler(db_path=self.tmp.name)
        sch.acquire("j1"); sch.acquire("j2"); sch.acquire("j3")
        self.assertFalse(sch.acquire("j4"))
        sch.release("j2")
        self.assertTrue(sch.acquire("j4"))    # 释放后回血

    def test_idempotent_job_key(self):
        sch = qs.QuotaScheduler(db_path=self.tmp.name)
        self.assertTrue(sch.acquire("same"))
        self.assertFalse(sch.acquire("same"))  # 重复投递同岗位→拒

    def test_plan1_always_before_plan2(self):
        cands = [
            {"plan_priority": "P2-A", "value_score": 99},
            {"plan_priority": "P1-D", "value_score": 40},
            {"plan_priority": "P1-A", "value_score": 45},
            {"plan_priority": "P1-A", "value_score": 90},
        ]
        ranked = qs.QuotaScheduler.rank(cands)
        self.assertEqual([c["plan_priority"] for c in ranked],
                         ["P1-A", "P1-A", "P1-D", "P2-A"])   # 高分P2也压不过P1（验收3）
        # 同档内按分数
        self.assertEqual(ranked[0]["value_score"], 90)

    def test_fit_truncates_at_remaining(self):
        self._force_low_cap()
        sch = qs.QuotaScheduler(db_path=self.tmp.name)
        cands = [{"plan_priority": "P1-A", "value_score": i} for i in range(10)]
        self.assertEqual(len(sch.fit(cands)), 3)   # 剩余额度=3，裁到3（验收4的机制面）


if __name__ == "__main__":
    unittest.main(verbosity=2)

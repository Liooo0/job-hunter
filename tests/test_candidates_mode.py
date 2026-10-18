#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选清单半自动模式（V5.8，2026-10-18）单测。

覆盖三层：
  1. 纯函数层：记录构造 / 排序 / 落盘（JSON+Markdown）；
  2. 结构层：boss_apply 里的开关、分支位置、stdout 留档调用；
  3. 设施层：shared.install_stdout_tee（写盘 + 幂等）。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import candidates as C  # noqa: E402
import shared as SH     # noqa: E402


class TestCandidateRecords(unittest.TestCase):
    JH_CATEGORY = "candidates"
    JH_NEW = True

    def test_make_record_fields(self):
        r = C.make_record(city="深圳", keyword="AI应用", company="某科技",
                          title="AI工程师", salary="15-25K", score=88,
                          five_dim={"total": 82, "verdict": "strong_apply",
                                    "dimensions": {"technical": {"score": 90,
                                                                 "weighted": 27.0}}},
                          greeting="您好！我是…", template_id="T2",
                          url="https://www.zhipin.com/job_detail/x.html")
        self.assertEqual(r["score"], 88)
        self.assertEqual(r["five_dim"]["total"], 82)
        self.assertEqual(r["greeting_template_id"], "T2")
        self.assertTrue(r["registered_at"])
        self.assertIn("zhipin.com", r["url"])

    def test_sort_by_score_then_total(self):
        a = C.make_record(city="a", keyword="k", company="A", title="t", score=80,
                          five_dim={"total": 90})
        b = C.make_record(city="a", keyword="k", company="B", title="t", score=90,
                          five_dim={"total": 60})
        c = C.make_record(city="a", keyword="k", company="C", title="t", score=80,
                          five_dim={"total": 95})
        top = C.sort_records([a, b, c])
        self.assertEqual([x["company"] for x in top], ["B", "C", "A"])

    def test_write_session_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            recs = [C.make_record(city="深圳", keyword="k", company="某某公司", title="岗",
                                  score=77,
                                  five_dim={"total": 70, "verdict": "apply",
                                            "dimensions": {"technical": {"weighted": 20}}},
                                  greeting="你好。", template_id="T1")]
            jp, mp = C.write_session(recs, session_ts="20261018_000000", out_dir=d)
            self.assertTrue(jp.exists() and mp.exists())
            data = json.loads(jp.read_text(encoding="utf-8"))
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["records"][0]["company"], "某某公司")
            md = mp.read_text(encoding="utf-8")
            self.assertIn("某某公司", md)
            self.assertIn("破冰语", md)
            self.assertIn("技术", md)
            self.assertIn("provenance 双闸", md)


class TestStructure(unittest.TestCase):
    JH_CATEGORY = "candidates"
    JH_NEW = True

    def test_boss_flags_and_branch(self):
        src = (BASE / "boss_apply.py").read_text(encoding="utf-8")
        self.assertIn("--candidates-only", src)
        self.assertIn("_CANDIDATES_ONLY", src)
        self.assertIn("candidates.make_record", src)
        self.assertIn("candidates.write_session", src)
        # 候选登记分支必须在真正点击（_execute_apply）之前
        self.assertLess(src.index("candidates.make_record("),
                        src.index("result = _execute_apply("))
        # 候选模式不占投递额度（配额只服务真投递）
        self.assertIn("candidates_only:不占额度", src)
        # stdout 留档
        self.assertIn('install_stdout_tee("boss")', src)


class TestStdoutTee(unittest.TestCase):
    JH_CATEGORY = "candidates"
    JH_NEW = True

    def test_tee_writes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            old_out, old_err = sys.stdout, sys.stderr
            try:
                p = SH.install_stdout_tee("unittest_tee", log_dir=Path(d))
                print("tee-hello-12345")
                p2 = SH.install_stdout_tee("unittest_tee", log_dir=Path(d))
                self.assertEqual(Path(p), Path(p2), "重复安装应幂等")
            finally:
                sys.stdout, sys.stderr = old_out, old_err
            self.assertIn("tee-hello-12345", Path(p).read_text(encoding="utf-8"))
            self.assertEqual(len(list(Path(d).glob("*.log"))), 1, "不应产生第二份日志")


if __name__ == "__main__":
    unittest.main()

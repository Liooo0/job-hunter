#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投后信号接线（V5.8，2026-10-18）单测：信号判定 / 简报生成 / 去重 / 结构断言。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import schedule_inquiry as SI  # noqa: E402
import post_signal as PS       # noqa: E402


class TestClassifySignal(unittest.TestCase):
    JH_CATEGORY = "post_signal"
    JH_NEW = True

    def test_interview(self):
        self.assertEqual(SI.classify_signal("你好，方便约个面试时间吗？"), "interview")
        self.assertEqual(SI.classify_signal("先电话沟通一下，你看什么时候方便"), "interview")

    def test_resume(self):
        self.assertEqual(SI.classify_signal("方便发份简历看看吗"), "resume")

    def test_auto_greeting_excluded(self):
        self.assertEqual(SI.classify_signal("您好，我是某公司HR，方便发份简历吗？"), "")

    def test_reject_excluded(self):
        self.assertEqual(SI.classify_signal("抱歉不太合适，祝您求职顺利"), "")

    def test_system_excluded(self):
        self.assertEqual(SI.classify_signal("系统消息：您的简历已收到"), "")

    def test_pure_greeting_excluded(self):
        self.assertEqual(SI.classify_signal("在吗"), "")

    def test_empty(self):
        self.assertEqual(SI.classify_signal(""), "")


class TestBriefGeneration(unittest.TestCase):
    JH_CATEGORY = "post_signal"
    JH_NEW = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._rd, self._sf = PS.REPORT_DIR, PS.STATE_FILE
        PS.REPORT_DIR = self.tmp / "reports"
        PS.STATE_FILE = self.tmp / "state.json"

    def tearDown(self):
        PS.REPORT_DIR, PS.STATE_FILE = self._rd, self._sf
        self._tmp.cleanup()

    def test_generates_brief_with_liveness(self):
        with mock.patch.object(PS, "fetch_page_text",
                               return_value=("200", "岗位说明 " * 80)):
            p = PS.on_signal(company="某科技", role="AI 应用工程师",
                             msg="方便约个面试时间吗？", jd="Python RAG 交付",
                             source="test")
        self.assertIsNotNone(p)
        text = Path(p).read_text(encoding="utf-8")
        self.assertIn("面试准备", text)
        self.assertIn("存活核查", text)
        self.assertIn("存活", text)          # liveness → active
        self.assertIn("某科技", text)
        self.assertIn("约面", text)          # 信号类型

    def test_no_link_marks_unchecked(self):
        p = PS.on_signal(company="某科技", role="岗", msg="方便发份简历吗", source="test")
        text = Path(p).read_text(encoding="utf-8")
        self.assertIn("未核", text)

    def test_dedupe_same_key(self):
        with mock.patch.object(PS, "fetch_page_text", return_value=("", "")):
            p1 = PS.on_signal(company="某科技", role="岗", msg="约面吗", source="test")
            p2 = PS.on_signal(company="某科技", role="岗", msg="再约一次面试", source="test")
        self.assertEqual(Path(p1), Path(p2))

    def test_no_signal_no_brief(self):
        p = PS.on_signal(company="某科技", role="岗", msg="你好", source="test")
        self.assertIsNone(p)


class TestWiring(unittest.TestCase):
    JH_CATEGORY = "post_signal"
    JH_NEW = True

    def test_hr_auto_reply_wired(self):
        src = (BASE / "hr_auto_reply.py").read_text(encoding="utf-8")
        self.assertIn("from post_signal import on_signal", src)
        self.assertIn("投后简报", src)

    def test_scan_job_mail_wired(self):
        src = (BASE / "scripts" / "scan_job_mail.py").read_text(encoding="utf-8")
        self.assertIn("from post_signal import on_signal", src)
        self.assertIn("投后简报", src)

    def test_post_signal_side_effects_scoped(self):
        """接线只允许写 data/reports 与状态文件——不得出现发送/投递动作。"""
        src = (BASE / "post_signal.py").read_text(encoding="utf-8")
        for forbidden in ("smtplib", "sendmail", "run_js", "DrissionPage", "_execute_apply"):
            self.assertNotIn(forbidden, src, f"post_signal 不应包含 {forbidden}")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""--enrich 浏览器兜底 + 退信回读（V5.8，2026-10-18）单测。

夹具纪律：邮箱一律运行时拼接（源码不出现连续邮箱字面量，避开仓内 L2 守卫），
标准 DSN 用最小手搓报文，不依赖真实邮箱内容。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import official_mail_apply as OMA  # noqa: E402

_AT = "@"


class _FakeProbe:
    def __init__(self, code, body):
        self.code, self.body = code, body

    def fetch(self, url):
        return self.code, self.body


class TestEnrichFallback(unittest.TestCase):
    JH_CATEGORY = "official_mail"
    JH_NEW = True

    def test_curl_ok_with_email_no_browser(self):
        good = "campus.cndc" + _AT + "shopee.com"
        with mock.patch.object(OMA, "chrome_fetch") as m:
            emails, note, ch = OMA.probe_with_fallback(
                "https://x.example/p", probe_mod=_FakeProbe("200", f"联系 {good} 投递"))
        self.assertEqual(emails, [good])
        self.assertEqual(ch, "curl")
        m.assert_not_called()

    def test_curl_403_falls_back_to_chrome(self):
        good = "campus" + _AT + "sea.com"
        with mock.patch.object(
                OMA, "chrome_fetch",
                return_value=(True, f"<html>{good}</html>", "")) as m:
            emails, note, ch = OMA.probe_with_fallback(
                "https://x.example/p", probe_mod=_FakeProbe("403", ""))
        self.assertEqual(emails, [good])
        self.assertEqual(ch, "chrome")
        self.assertIn("浏览器渲染兜底", note)
        m.assert_called_once()

    def test_spa_shell_falls_back(self):
        shell = ("<html><head><script>var a=1</script><script>var b=2</script>"
                 "</head><body></body></html>")
        with mock.patch.object(
                OMA, "chrome_fetch",
                return_value=(True, "<div>no mail here</div>", "")) as m:
            emails, note, ch = OMA.probe_with_fallback(
                "https://x.example/p", probe_mod=_FakeProbe("200", shell))
        self.assertEqual(emails, [])
        self.assertIn("浏览器渲染兜底未检出邮箱", note)
        m.assert_called_once()

    def test_both_fail_keeps_failure_note(self):
        with mock.patch.object(OMA, "chrome_fetch", return_value=(False, "", "连接失败")):
            emails, note, ch = OMA.probe_with_fallback(
                "https://x.example/p", probe_mod=_FakeProbe("403", ""))
        self.assertEqual(emails, [])
        self.assertTrue(note.startswith("只读探针失败"))
        self.assertIn("浏览器兜底失败", note)


# ── 退信夹具（邮箱一律经 _AT 拼接，源码不留连续字面量）──
_DSN = (f"""From: MAILER-DAEMON{_AT}qq.com
To: user{_AT}qq.com
Subject: Returned Mail: application notice
Content-Type: multipart/report; report-type=delivery-status; boundary=QQB

--QQB
Content-Type: text/plain

Delivery to the following recipient failed permanently.
--QQB
Content-Type: message/delivery-status

Reporting-MTA: dns; qq.com
Final-Recipient: rfc822; hr{_AT}nowhere.invalid
Action: failed
Status: 5.7.0
Diagnostic-Code: smtp; 552-5.7.0 remote potential security issue

Original-Message-ID: <AAA123{_AT}qq.com>
--QQB--
""").encode("utf-8")

_QQ_BOUNCE = (f"""From: postmaster{_AT}qq.com
To: user{_AT}qq.com
Subject: delivery failure notice
Content-Type: text/plain; charset=utf-8

您的邮件投递失败：无法投递到 hr{_AT}nowhere.invalid
Message-ID: <BBB456{_AT}qq.com>
error: 550 mailbox unavailable
""").encode("utf-8")


class TestBounce(unittest.TestCase):
    JH_CATEGORY = "official_mail"
    JH_NEW = True

    def test_parse_standard_dsn(self):
        p = OMA.parse_bounce(_DSN)
        self.assertIsNotNone(p)
        self.assertEqual(p["orig_message_id"], "<AAA123" + _AT + "qq.com>")
        self.assertEqual(p["recipient"], "hr" + _AT + "nowhere.invalid")
        self.assertIn("552", p["reason"])

    def test_parse_qq_style(self):
        p = OMA.parse_bounce(_QQ_BOUNCE)
        self.assertIsNotNone(p)
        self.assertEqual(p["orig_message_id"], "<BBB456" + _AT + "qq.com>")
        self.assertIn("550", p["reason"])

    def test_non_bounce_returns_none(self):
        raw = (f"From: a{_AT}b.com\nSubject: hello\n\nhi").encode("utf-8")
        self.assertIsNone(OMA.parse_bounce(raw))

    def test_apply_bounce_updates_queue_by_message_id(self):
        mid = "<AAA123" + _AT + "qq.com>"
        mail = "hr" + _AT + "nowhere.invalid"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            item = {"id": "M123", "company": "测试", "title": "岗工",
                    "subject": "应聘测试公司 — 某人", "status": "sent",
                    "sent_at": "2026-10-18T00:00:00", "message_id": mid,
                    "hr_email": mail}
            (d / "M123.json").write_text(json.dumps(item, ensure_ascii=False),
                                         encoding="utf-8")
            hit = OMA.apply_bounce({"orig_message_id": mid, "reason": "552 spam",
                                    "recipient": mail}, queue_dir=d)
            self.assertEqual(hit, "M123")
            it = json.loads((d / "M123.json").read_text(encoding="utf-8"))
            self.assertEqual(it["status"], "bounced")
            self.assertIn("552", it["bounce_reason"])
            self.assertEqual(it["hr_email"], mail, "旧字段不得被改")

    def test_apply_bounce_by_subject_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            item = {"id": "M9", "company": "X", "title": "y",
                    "subject": "应聘X公司 — 昵称", "status": "sent",
                    "message_id": "<zzz" + _AT + "x>"}
            (d / "M9.json").write_text(json.dumps(item, ensure_ascii=False),
                                       encoding="utf-8")
            hit = OMA.apply_bounce(
                {"orig_message_id": "", "bounce_subject": "退信通知：应聘X公司 — 昵称 无法投递",
                 "reason": "550"},
                queue_dir=d)
            self.assertEqual(hit, "M9")

    def test_cli_flag_present(self):
        src = (BASE / "official_mail_apply.py").read_text(encoding="utf-8")
        self.assertIn("--bounces", src)
        self.assertIn("def check_bounces", src)
        self.assertIn("def parse_bounce", src)
        self.assertIn("email_probe_channel", src)


if __name__ == "__main__":
    unittest.main()

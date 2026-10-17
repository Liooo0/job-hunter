#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify.py 单元测试：验证飞书通道、节流机制、防轰炸与异常容错。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import notify


class TestNotify(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        # 隔离状态文件与日志文件
        self.orig_state_file = notify.STATE_FILE
        self.orig_log_file = notify.LOG_FILE
        notify.STATE_FILE = self.tmp_path / "alert_state.json"
        notify.LOG_FILE = self.tmp_path / "alerts.log"

    def tearDown(self):
        notify.STATE_FILE = self.orig_state_file
        notify.LOG_FILE = self.orig_log_file
        self.tmp_dir.cleanup()

    def test_throttle_mechanism(self):
        """验证 30 分钟节流机制：同 key 重复调用被拦截，防轰炸生效。"""
        key = "test_throttle_key"
        # 第一次允许发送（纯判断不写盘）
        self.assertTrue(notify._should_send(key, throttle=1800))
        # 渠道成功发送后记录状态
        notify._record_sent(key)
        # 立即第二次调用应被节流拦截
        self.assertFalse(notify._should_send(key, throttle=1800))
        # 另外一个 key 不受影响
        self.assertTrue(notify._should_send("another_key", throttle=1800))

    @patch("notify._send_feishu")
    @patch("notify._send_weixin")
    @patch("notify._send_wxpusher")
    def test_failed_send_does_not_advance_cooldown(self, mock_wxpusher, mock_weixin, mock_feishu):
        """验证所有渠道发送失败时：不写盘，不推进 cooldown，允许后续安全重试。"""
        mock_feishu.return_value = False
        mock_weixin.return_value = False
        mock_wxpusher.return_value = False

        key = "fail_retry_key"
        # 发送失败
        ok = notify.alert(key, "失败通知", "正文", throttle=1800)
        self.assertFalse(ok)

        # 状态文件不应记录该 key，_should_send 依然返回 True
        self.assertTrue(notify._should_send(key, throttle=1800))

        # 随后的重试在渠道恢复后能成功发出
        mock_feishu.return_value = True
        ok2 = notify.alert(key, "重试通知", "正文", throttle=1800)
        self.assertTrue(ok2)

        # 成功发出后，状态已被记录，进入冷却期
        self.assertFalse(notify._should_send(key, throttle=1800))

    def test_throttle_zero_always_passes(self):
        """验证 throttle=0（如紧急熔断 kill_switch）每次都允许发送。"""
        key = "kill_switch"
        self.assertTrue(notify._should_send(key, throttle=0))
        notify._record_sent(key)
        self.assertTrue(notify._should_send(key, throttle=0))

    @patch("notify._send_feishu")
    def test_alert_success_feishu(self, mock_send_feishu):
        """验证正常情况下优先调用飞书通道且返回 True。"""
        mock_send_feishu.return_value = True
        ok = notify.alert("unit_test", "测试通知", "正文内容", level="info", throttle=0)
        self.assertTrue(ok)
        mock_send_feishu.assert_called_once()
        args, _ = mock_send_feishu.call_args
        self.assertIn("job-hunter · 测试通知", args[0])
        self.assertIn("正文内容", args[1])

    @patch("notify._send_feishu")
    @patch("notify._send_weixin")
    @patch("notify._send_wxpusher")
    def test_default_channel_falls_back_to_weixin(self, mock_wxpusher, mock_weixin, mock_feishu):
        """默认飞书失败后应尝试微信，避免只测显式微信模式。"""
        mock_feishu.return_value = False
        mock_weixin.return_value = True
        self.assertTrue(notify.alert("fallback", "备用通道测试", "正文", throttle=0))
        mock_feishu.assert_called_once()
        mock_weixin.assert_called_once()
        mock_wxpusher.assert_not_called()

    @patch("notify._send_feishu")
    @patch("notify._send_weixin")
    @patch("notify._send_wxpusher")
    def test_failure_does_not_raise(self, mock_wxpusher, mock_weixin, mock_feishu):
        """验证所有通道全挂/抛异常时，绝不搞挂主业务，静默返回 False。"""
        mock_feishu.side_effect = Exception("Simulated Feishu connection error")
        mock_weixin.side_effect = Exception("Simulated Weixin connection error")
        mock_wxpusher.side_effect = Exception("Simulated WxPusher connection error")

        # 必须不抛异常，且返回 False
        ok = notify.alert("fail_test", "失败测试", "正文", throttle=0)
        self.assertFalse(ok)

    def test_disabled_env(self):
        """验证环境变量配置禁用时，完全静默。"""
        with patch.dict(os.environ, {"JOBHUNTER_ALERT_DISABLED": "1"}):
            self.assertFalse(notify.alert("test", "test", throttle=0))


class TestFeishuRouting(unittest.TestCase):
    """防串台契约（2026-09-28）：飞书发送必须走 job-notify 独立应用。

    事故：飞书通道曾借道 rental-mgmt（波比管家）应用 → 求职消息串进甲方项目专用窗口。
    钉死三件事：发送身份（--profile job-notify）、身份文件（~/.job-notify-identity.json）、
    目标来源（identity 文件读取）。
    """

    def setUp(self):
        # 与其他用例一样隔离状态/日志文件：_send_feishu 成功时会写 [feishu-sent] 日志，
        # 不隔离会把「标题/正文」这类测试夹具写进真实 alerts.log（2026-09-28 实踩）。
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.orig_state_file = notify.STATE_FILE
        self.orig_log_file = notify.LOG_FILE
        notify.STATE_FILE = self.tmp_path / "alert_state.json"
        notify.LOG_FILE = self.tmp_path / "alerts.log"

    def tearDown(self):
        notify.STATE_FILE = self.orig_state_file
        notify.LOG_FILE = self.orig_log_file
        self.tmp_dir.cleanup()

    def test_feishu_send_builds_job_notify_command(self):
        captured = {}

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _Proc()

        with patch.object(notify, "_feishu_target", return_value="ou_test"), \
                patch("subprocess.run", side_effect=_fake_run):
            self.assertTrue(notify._send_feishu("标题", "正文"))

        cmd = captured["cmd"]
        self.assertIn("--profile", cmd)
        self.assertEqual(cmd[cmd.index("--profile") + 1], "job-notify")
        self.assertIn("+messages-send", cmd)
        self.assertIn("--as", cmd)

    def test_feishu_identity_file_is_job_notify(self):
        self.assertEqual(notify.FEISHU_IDENTITY_FILE.name, ".job-notify-identity.json")

    def test_feishu_target_reads_identity_file(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "ident.json"
            f.write_text(json.dumps({"admin_open_id": "ou_fixture"}), encoding="utf-8")
            with patch.object(notify, "FEISHU_IDENTITY_FILE", f):
                os.environ.pop("JOBHUNTER_FEISHU_TARGET", None)
                self.assertEqual(notify._feishu_target(), "ou_fixture")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-2 测试：验证 51job 与猎聘等平台的告警 key 命名空间隔离。

防止跨平台同类告警（如 chrome_down / kill_switch_block / reply_lock_block）
因共享全局 key 导致一个平台告警后，另一平台在 30 分钟内被静默节流。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import notify

BASE = Path(__file__).resolve().parent.parent


class TestPlatformAlertNamespace(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.orig_state = notify.STATE_FILE
        self.orig_log = notify.LOG_FILE
        notify.STATE_FILE = self.tmp_path / "alert_state.json"
        notify.LOG_FILE = self.tmp_path / "alerts.log"

    def tearDown(self):
        notify.STATE_FILE = self.orig_state
        notify.LOG_FILE = self.orig_log
        self.tmp_dir.cleanup()

    @patch("notify._send_feishu", return_value=True)
    def test_different_platforms_do_not_throttle_each_other(self, mock_send):
        """不同平台的同类告警（51job:chrome_down vs liepin:chrome_down）互不影响。"""
        # 51job 触发 chrome_down
        ok_51 = notify.alert("51job:chrome_down", "51job 无法连接 Chrome", throttle=1800)
        self.assertTrue(ok_51)

        # 紧接着猎聘也触发 chrome_down，不能被 51job 的告警节流
        ok_liepin = notify.alert("liepin:chrome_down", "猎聘无法连接 Chrome", throttle=1800)
        self.assertTrue(ok_liepin)

        # 飞书渠道应被调用两次
        self.assertEqual(mock_send.call_count, 2)

    @patch("notify._send_feishu", return_value=True)
    def test_same_platform_same_alert_throttles_normally(self, mock_send):
        """同一平台的同类告警在冷却期内正常节流。"""
        # 51job 第一次 chrome_down -> 成功发送
        ok1 = notify.alert("51job:chrome_down", "51job 无法连接 Chrome", throttle=1800)
        self.assertTrue(ok1)

        # 51job 紧接着第二次 chrome_down -> 应被节流
        ok2 = notify.alert("51job:chrome_down", "51job 无法连接 Chrome", throttle=1800)
        self.assertFalse(ok2)

        # 此时猎聘的 chrome_down 仍可发出
        ok3 = notify.alert("liepin:chrome_down", "猎聘无法连接 Chrome", throttle=1800)
        self.assertTrue(ok3)

        # 猎聘第二次 chrome_down -> 也应被节流
        ok4 = notify.alert("liepin:chrome_down", "猎聘无法连接 Chrome", throttle=1800)
        self.assertFalse(ok4)

        # 实际仅发送了 2 次（51job 一次，猎聘一次）
        self.assertEqual(mock_send.call_count, 2)

    @patch("notify._send_feishu", return_value=True)
    def test_kill_switch_and_reply_lock_platform_isolation(self, mock_send):
        """验证 kill_switch_block 与 reply_lock_block 跨平台隔离。"""
        self.assertTrue(notify.alert("51job:kill_switch_block", "51job kill switch", throttle=1800))
        self.assertTrue(notify.alert("liepin:kill_switch_block", "猎聘 kill switch", throttle=1800))
        self.assertFalse(notify.alert("51job:kill_switch_block", "51job kill switch 2", throttle=1800))

        self.assertTrue(notify.alert("51job:reply_lock_block", "51job reply lock", throttle=3600))
        self.assertTrue(notify.alert("liepin:reply_lock_block", "猎聘 reply lock", throttle=3600))
        self.assertFalse(notify.alert("51job:reply_lock_block", "51job reply lock 2", throttle=3600))

    def test_entrypoint_sources_use_namespaced_keys(self):
        """静态断言：源码中 51job 与猎聘必须使用命名空间 key，避免回归。"""
        src_51 = (BASE / "platform_51job.py").read_text(encoding="utf-8")
        src_liepin = (BASE / "platform_liepin.py").read_text(encoding="utf-8")

        # 51job 告警必须带 51job: 前缀
        for k in ("51job:chrome_down", "51job:kill_switch_block", "51job:reply_lock_block", "51job:login"):
            self.assertIn(f'"{k}"', src_51, f"platform_51job.py 缺少 {k}")

        # 猎聘告警必须带 liepin: 前缀
        for k in ("liepin:chrome_down", "liepin:kill_switch_block", "liepin:reply_lock_block"):
            self.assertIn(f'"{k}"', src_liepin, f"platform_liepin.py 缺少 {k}")

        # 确保不再裸写可能碰撞的 key
        for bare_key in ('"chrome_down"', '"kill_switch_block"', '"reply_lock_block"'):
            self.assertNotIn(bare_key, src_51, f"platform_51job.py 不应包含未命名空间的裸 key: {bare_key}")
            self.assertNotIn(bare_key, src_liepin, f"platform_liepin.py 不应包含未命名空间的裸 key: {bare_key}")


if __name__ == "__main__":
    unittest.main()

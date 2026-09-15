#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5.5 测试（2026-09-15）：告警触达 + 多平台投递状态口径

覆盖两件事：
A. notify.py —— 告警是「出了事没人知道」的补丁，它自己出事不能反过来搞挂投递：
   关闭开关、节流、缺凭据、网络异常四条路径都必须安静返回 False，且不抛异常。
B. 多平台投递状态口径 —— 51job / 猎聘 的成功分支原本写死 UNCERTAIN，
   这里用源码断言钉住「成功分支必须写 APPLIED + verified=1」，
   防止以后改动把这条口径再改回去（真正跑浏览器的那条路径无法在单测里模拟）。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import notify  # noqa: E402


class TestNotifySafety(unittest.TestCase):
    """告警模块的四条降级路径。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch_state = mock.patch.object(notify, "STATE_FILE",
                                              Path(self._tmp.name) / "state.json")
        self._patch_log = mock.patch.object(notify, "LOG_FILE",
                                            Path(self._tmp.name) / "alerts.log")
        self._patch_state.start()
        self._patch_log.start()
        self.addCleanup(self._patch_state.stop)
        self.addCleanup(self._patch_log.stop)
        # 环境变量在每个用例里单独设置，避免互相污染
        self.addCleanup(os.environ.pop, "JOBHUNTER_ALERT_DISABLED", None)
        self.addCleanup(os.environ.pop, "JOBHUNTER_ALERT_MIN_INTERVAL", None)

    def test_disabled_switch_is_silent(self):
        os.environ["JOBHUNTER_ALERT_DISABLED"] = "1"
        with mock.patch("urllib.request.urlopen") as uo:
            self.assertFalse(notify.alert("k", "标题", "正文", throttle=0))
        uo.assert_not_called()

    def test_no_credentials_returns_false(self):
        with mock.patch.object(notify, "_load_credentials", return_value=("", "")):
            self.assertFalse(notify.alert("k", "标题", "正文", throttle=0))

    def test_network_error_is_swallowed(self):
        with mock.patch.object(notify, "_load_credentials", return_value=("tok", "uid")), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("网络断了")):
            self.assertFalse(notify.alert("k", "标题", "正文", throttle=0))  # 不抛异常

    def test_success_path_sends_markdown(self):
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"code": 1000, "msg": "发送成功"}).encode()

        def _fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        with mock.patch.object(notify, "_load_credentials", return_value=("tok", "uid")), \
                mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            self.assertTrue(notify.alert("k", "标题", "正文", level="error", throttle=0))

        self.assertEqual(captured["body"]["contentType"], 3)
        self.assertEqual(captured["body"]["uids"], ["uid"])
        self.assertIn("job-hunter", captured["body"]["summary"])
        self.assertIn("🛑", captured["body"]["summary"])

    def test_throttle_blocks_second_send(self):
        calls = []

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"code": 1000}).encode()

        def _fake_urlopen(req, timeout=None):
            calls.append(1)
            return _Resp()

        with mock.patch.object(notify, "_load_credentials", return_value=("tok", "uid")), \
                mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            first = notify.alert("same-key", "标题", "正文", throttle=1800)
            second = notify.alert("same-key", "标题", "正文", throttle=1800)
            third = notify.alert("same-key", "标题", "正文", throttle=0)   # throttle=0 必推

        self.assertTrue(first)
        self.assertFalse(second, "同一 key 在节流窗口内不应重复推送")
        self.assertTrue(third, "throttle=0 的告警必须每次都推")
        self.assertEqual(len(calls), 2)


class TestPlatformStatusContract(unittest.TestCase):
    """多平台成功分支的状态口径（源码级断言）。"""

    def _source(self, name):
        return (BASE / name).read_text(encoding="utf-8")

    def _strip_comments(self, text: str) -> str:
        """去掉整行注释——注释里会引用被修掉的旧写法（如 status="UNCERTAIN"），
        不剔除会让断言把「说明文字」误判成「代码」。"""
        return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))

    def test_51job_success_branch_writes_applied(self):
        src = self._source("platform_51job.py")
        guard = 'if "已申请" in state or "已投递" in state:'
        self.assertIn(guard, src)
        # 注意：这个守卫在 51job 里出现两次（click_apply_and_check 的返回值判断
        # 和 run_city_keyword 的落库分支），落库的是**最后一次**出现。
        branch = self._strip_comments(src.rsplit(guard, 1)[1][:1500])
        self.assertIn('status="APPLIED"', branch, "51job 成功分支必须写 APPLIED")
        self.assertIn("verified=1", branch, "51job 成功分支必须写 verified=1")
        self.assertNotIn('status="UNCERTAIN"', branch,
                         "成功分支不得再写 UNCERTAIN（2026-09-15 修的就是这个）")

    def test_liepin_success_branch_writes_applied(self):
        src = self._source("platform_liepin.py")
        guard = 'if "已申请" in state or "已投递" in state:'
        self.assertIn(guard, src)
        branch = self._strip_comments(src.rsplit(guard, 1)[1][:1500])
        self.assertIn('status="APPLIED"', branch, "猎聘成功分支必须写 APPLIED")
        self.assertIn("verified=1", branch)
        self.assertNotIn('status="UNCERTAIN"', branch)

    def test_failure_branch_records_failed(self):
        """失败也要落库（以前只 print），且 decision 不能占闸门额度。"""
        for name in ("platform_51job.py", "platform_liepin.py"):
            src = self._source(name)
            self.assertIn('status="FAILED"', src, f"{name} 失败分支应落库 FAILED")
            self.assertIn('decision="failed"', src,
                          f"{name} 失败记录的 decision 必须是 failed（不占日/小时额度）")

    def test_alerts_wired_into_entrypoints(self):
        for name, keys in (
            ("boss_apply.py", ("boss_risk", "boss_login", "slowdown_stop", "kill_switch")),
            ("platform_51job.py", ("login", "chrome_down", "tab_lost")),
        ):
            src = self._source(name)
            for k in keys:
                self.assertIn(f'"{k}"', src, f"{name} 缺少 {k} 告警接线")


if __name__ == "__main__":
    unittest.main()

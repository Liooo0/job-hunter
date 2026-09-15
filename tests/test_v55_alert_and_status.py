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
        # 这些用例验证的是 wxpusher 备用通道的降级路径，所以显式指定通道
        os.environ["JOBHUNTER_ALERT_CHANNEL"] = "wxpusher"
        self.addCleanup(os.environ.pop, "JOBHUNTER_ALERT_CHANNEL", None)

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


class TestGuardsWiring(unittest.TestCase):
    """本轮体检发现的两个「说了但没做到」的护栏。"""

    def _source(self, name):
        return (BASE / name).read_text(encoding="utf-8")

    def test_platform_scripts_check_kill_switch(self):
        """shared.py 顶部明写「任何脚本写操作前必须调 kill_switch_check()」，
        但 51job / 猎聘 此前一行都没调 —— 翻急停时它们照投不误。"""
        for name in ("platform_51job.py", "platform_liepin.py"):
            self.assertIn("kill_switch_check", self._source(name),
                          f"{name} 必须在写操作前检查 kill switch")

    def test_run_daily_port_matches_actual_port(self):
        """run_daily.sh 原来查 9222，而脚本实际用 9223 → 就绪检查永远失败、
        直接 exit 1，launchd 那条路从来没跑起来。"""
        daily = self._source("run_daily.sh")
        self.assertIn('CHROME_PORT="${CHROME_PORT:-', daily)
        self.assertNotIn("127.0.0.1:9222", daily, "run_daily.sh 不应再硬编码 9222")
        for name in ("boss_apply.py", "platform_51job.py"):
            self.assertIn("9223", self._source(name),
                          f"{name} 的调试端口应与 run_daily.sh 一致（9223）")


class TestWeixinChannel(unittest.TestCase):
    """微信（iLink）通道：默认走它，失败自动降级到 wxpusher。"""

    def setUp(self):
        for k in ("JOBHUNTER_ALERT_CHANNEL", "JOBHUNTER_WEIXIN_TARGET",
                  "JOBHUNTER_ALERT_DISABLED", "JOBHUNTER_HERMES_BIN"):
            os.environ.pop(k, None)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in (
            "JOBHUNTER_ALERT_CHANNEL", "JOBHUNTER_WEIXIN_TARGET",
            "JOBHUNTER_ALERT_DISABLED", "JOBHUNTER_HERMES_BIN")])
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name, attr in (("STATE_FILE", "state.json"), ("LOG_FILE", "alerts.log")):
            p = mock.patch.object(notify, name, Path(self._tmp.name) / attr)
            p.start()
            self.addCleanup(p.stop)

    def test_target_prefers_env_then_hermes_env(self):
        os.environ["JOBHUNTER_WEIXIN_TARGET"] = "o9cq123@im.wechat"
        self.assertEqual(notify._weixin_target(), "weixin:o9cq123@im.wechat")
        os.environ.pop("JOBHUNTER_WEIXIN_TARGET")
        # 没配环境变量时回落 Hermes 网关自己的 .env（本机已配好，应当读得到）
        self.assertTrue(notify._weixin_target().startswith("weixin:o9cq"),
                        "应能从 ~/.hermes/.env 的 WEIXIN_ALLOWED_USERS 读到 uid")

    def test_weixin_success_short_circuits(self):
        os.environ["JOBHUNTER_WEIXIN_TARGET"] = "weixin:aaaa@im.wechat"
        with mock.patch.object(notify, "_send_weixin", return_value=True) as w, \
                mock.patch.object(notify, "_send_wxpusher") as x:
            self.assertTrue(notify.alert("k", "标题", "正文", throttle=0))
        w.assert_called_once()
        x.assert_not_called()

    def test_weixin_failure_falls_back_to_wxpusher(self):
        os.environ["JOBHUNTER_WEIXIN_TARGET"] = "weixin:aaaa@im.wechat"
        with mock.patch.object(notify, "_send_weixin", return_value=False), \
                mock.patch.object(notify, "_send_wxpusher", return_value=True) as x:
            self.assertTrue(notify.alert("k", "标题", "正文", throttle=0),
                            "微信通道失败时不能把告警丢进黑洞")
        x.assert_called_once()

    def test_send_weixin_builds_hermes_send_command(self):
        os.environ["JOBHUNTER_WEIXIN_TARGET"] = "weixin:aaaa@im.wechat"
        os.environ["JOBHUNTER_HERMES_BIN"] = "/usr/local/bin/hermes"
        captured = {}

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _P()

        with mock.patch("subprocess.run", side_effect=_fake_run):
            self.assertTrue(notify._send_weixin("标题", "正文"))

        cmd = captured["cmd"]
        self.assertEqual(cmd[:3], ["/usr/local/bin/hermes", "send", "--to"])
        self.assertIn("weixin:aaaa@im.wechat", cmd)
        self.assertIn("--subject", cmd)


if __name__ == "__main__":
    unittest.main()

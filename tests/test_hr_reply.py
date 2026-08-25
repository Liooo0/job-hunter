#!/usr/bin/env python3
"""hr_auto_reply 归档依赖修复单元测试（P2-T3）。

背景：main() 原来写 `from boss_full import scan_messages`，但 boss_full.py 已移入
archive/legacy/，运行到该路径直接 ModuleNotFoundError。
修复：扫描逻辑内联为 _scan_chat_page(unread_only)（未读模式=原 scan_messages 行为），
不再引用任何归档模块。

说明：真连 Chrome 的端到端扫描不在单测范围（会动真实浏览器会话）；
这里用假 DrissionPage 模块让 ChromiumPage 连接抛错，验证异常分支优雅返回 []。

跑法一（unittest discover，从项目根目录）:
    PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -t . -v

跑法二（直接运行）:
    cd tests && PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 test_hr_reply.py
"""
import sys
import types
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import hr_auto_reply  # noqa: E402


class TestNoArchivedDependency(unittest.TestCase):
    def test_source_has_no_boss_full_reference(self):
        src = (PROJECT / "hr_auto_reply.py").read_text(encoding="utf-8")
        self.assertNotIn("from boss_full", src, "不应再 import 已归档的 boss_full 模块")
        self.assertNotIn("import boss_full", src, "不应再 import 已归档的 boss_full 模块")

    def test_import_ok(self):
        self.assertTrue(hasattr(hr_auto_reply, "scan_unread_messages"))


class TestMessageFilter(unittest.TestCase):
    def test_self_and_system_msgs_filtered(self):
        self.assertFalse(hr_auto_reply._is_hr_real_message(""))
        self.assertFalse(hr_auto_reply._is_hr_real_message("好"))
        self.assertFalse(hr_auto_reply._is_hr_real_message("您好！我是刘文迪，求职方向AI应用"))
        self.assertFalse(hr_auto_reply._is_hr_real_message("好的，谢谢您告知，祝您招聘顺利！"))
        self.assertFalse(hr_auto_reply._is_hr_real_message("您正在与Boss张三沟通"))
        self.assertFalse(hr_auto_reply._is_hr_real_message("您的附件简历已送达"))
        self.assertFalse(hr_auto_reply._is_hr_real_message("查看详细分析"))

    def test_real_hr_msg_passes(self):
        self.assertTrue(hr_auto_reply._is_hr_real_message("方便发一份简历过来看看吗？"))


class TestScanGracefulDegradation(unittest.TestCase):
    def test_returns_empty_when_chrome_down(self):
        fake = types.ModuleType("DrissionPage")

        class FakeChromiumPage:
            def __init__(self, port):
                raise ConnectionRefusedError("测试模拟：Chrome 未启动")

        fake.ChromiumPage = FakeChromiumPage
        saved = sys.modules.get("DrissionPage")
        sys.modules["DrissionPage"] = fake
        try:
            self.assertEqual(hr_auto_reply.scan_unread_messages(), [])
            self.assertEqual(hr_auto_reply.scan_all_conversations(), [])
        finally:
            if saved is not None:
                sys.modules["DrissionPage"] = saved
            else:
                sys.modules.pop("DrissionPage", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)

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
        # 2026-09-15: 不再写死真名——公开仓库不留 PII；本地配了真名时该用例照样成立
        _n = hr_auto_reply.MY_NAME or "张三"
        self.assertFalse(hr_auto_reply._is_hr_real_message(f"您好！我是{_n}，求职方向AI应用"))
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


class TestHangDeadlockGuard(unittest.TestCase):
    """2026-09-16 回归：某个 tab 的 renderer 卡死不能把整个扫描挂住。

    当天事实：zhipin 三个 tab + goofish tab 的 renderer 卡在加载中，
    DrissionPage 读 .url 会一直等 → 扫描永久挂起。修法：改用目标级列表
    (HTTP /json/list) 只挑聊天页 tab，卡死的用 Page.reload 救活。
    """

    def setUp(self):
        self.src = (PROJECT / "hr_auto_reply.py").read_text(encoding="utf-8")

    def test_no_longer_loops_all_tabs_reading_url(self):
        self.assertNotIn("for tid in page.tab_ids", self.src,
                         "遍历所有 tab 读 url 会把扫描挂死，必须走目标级列表")

    def test_uses_target_level_lookup_and_revive(self):
        self.assertIn("def _find_chat_tab", self.src)
        self.assertIn("json/list", self.src)
        self.assertIn("Page.reload", self.src)

    def test_own_sent_message_is_not_treated_as_hr_message(self):
        """Boss 会话最后一句是自己发的回复时，不能再起草一遍回给自己。"""
        own = {"目前离职状态，随时能到岗。简历发你？"}
        self.assertTrue(hr_auto_reply._is_own_text("目前离职状态，随时能到岗。简历发你？", own))
        self.assertTrue(hr_auto_reply._is_own_text("目前离职状态， 随时能到岗。简历发你？", own),
                        "空白差异不应影响判定")
        self.assertFalse(hr_auto_reply._is_own_text("你好，方便发份简历吗？", own))
        self.assertFalse(hr_auto_reply._is_own_text("", own))
        self.assertFalse(hr_auto_reply._is_own_text("随便一句话", set()))
        self.assertIsInstance(hr_auto_reply._own_sent_texts(), set)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聊天状态回填的判定逻辑测试（2026-09-15）

这层逻辑在真机上踩过三次坑，必须用单测钉住（都不需要浏览器，全是纯函数）：

1. 语料里存在「您好」这种 2 字消息 —— 拿它做前缀匹配，会把**所有以「您好」
   开头的招呼语判成 HR 消息**（于是 5 条根本没回复的会话被标成"已回复"）。
2. Boss 的系统占位「您正在与BossX沟通」带着未读角标 —— 先看角标就会把它
   判成 HR 回复。
3. HR 没回话时，留痕文件里的 `message` 字段其实是**我自己发出去的招呼语**，
   当成 HR 语料就会把招呼语判成 HR 回复。

判定原则（用户定稿）：只认硬特征，不确定标 unknown，宁可不写也不写错。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))

import sync_chat_status as S  # noqa: E402

GREETING = ("您好！我是Lio，主攻 AI 应用与工作流落地。我拥有"
            "移动通信技术 + 工商管理的复合背景，深谙 Agent 编排与闭环工作流搭建。")
PLACEHOLDER = "您正在与Boss陈先生沟通"


class TestCorpusHit(unittest.TestCase):
    """最短文本不得当前缀模式用。"""

    def test_short_corpus_text_does_not_prefix_match(self):
        self.assertIsNone(S._corpus_hit(GREETING, {"您好"}),
                          "2 字的「您好」不能把以您好开头的招呼语判成命中")

    def test_exact_match_always_wins(self):
        self.assertEqual(S._corpus_hit("您好", {"您好"}), "您好")

    def test_long_prefix_match_works(self):
        long_hr = "同学你好，我们招27年毕业的哈"
        self.assertEqual(S._corpus_hit("同学你好，我们招27年毕业的哈", {long_hr}), long_hr)


class TestClassify(unittest.TestCase):
    """谁说的：hr / mine / unknown。"""

    def setUp(self):
        self.mine = {f"您好！我是{__import__('os').environ.get('USER', 'x')}"}  # 占位，下面覆盖
        self.mine = {"好的，方便发一下具体的岗位JD吗？我想看下是否匹配"}
        self.hr = {"[祈祷] 不好意思，不太合适哦", "同学你好，我们招27年毕业的哈"}

    def c(self, msg, unread=0):
        return S._classify_last_message(msg, unread, self.mine, self.hr)

    def test_hr_text_from_corpus_is_hr(self):
        self.assertEqual(self.c("[祈祷] 不好意思，不太合适哦"), "hr")

    def test_greeting_with_unread_is_mine(self):
        self.assertEqual(self.c(GREETING, unread=3), "mine",
                         "招呼语带未读角标也不能算 HR 回复")

    def test_system_placeholder_with_unread_is_mine(self):
        self.assertEqual(self.c(PLACEHOLDER, unread=2), "mine",
                         "Boss 系统占位常带未读角标，不是 HR 说的话")

    def test_my_known_reply_is_mine(self):
        self.assertEqual(self.c("好的，方便发一下具体的岗位JD吗？我想看下是否匹配"), "mine")

    def test_unknown_opener_is_unknown(self):
        """我的回复风格是短版直给（好的/了解 起手），命不中留痕时判 unknown，不写库。"""
        msg = "了解，人工评测为主的岗位我可以考虑。我做过RAG匹配引擎和95分球鞋监控"
        self.assertEqual(self.c(msg), "unknown")

    def test_empty_is_unknown(self):
        self.assertEqual(self.c(""), "unknown")

    def test_unread_without_corpus_hit_is_hr(self):
        self.assertEqual(self.c("方便聊一下你的经历吗", unread=1), "hr")


class TestCorpusLoading(unittest.TestCase):
    """留痕语料要把「我的招呼语 / 系统占位」排除出 HR 语料。"""

    def test_greeting_and_placeholder_excluded_from_hr_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "sent_replies"
            d.mkdir()
            (d / "a.json").write_text(json.dumps({
                "message": GREETING,             # 其实是自己发的招呼语
                "my_reply": "",
            }, ensure_ascii=False), encoding="utf-8")
            (d / "b.json").write_text(json.dumps({
                "message": PLACEHOLDER,          # Boss 系统占位
                "my_reply": "",
            }, ensure_ascii=False), encoding="utf-8")
            (d / "c.json").write_text(json.dumps({
                "message": "[祈祷] 不好意思，不太合适哦",   # 真的 HR 消息
                "my_reply": "好的，方便发一下具体的岗位JD吗？",
            }, ensure_ascii=False), encoding="utf-8")

            with mock.patch.object(S, "BASE", tmp):
                mine, theirs = S._load_reply_corpus()

        self.assertIn("好的，方便发一下具体的岗位JD吗？", mine)
        self.assertNotIn(GREETING, mine, "my_reply 为空时不该把 message 当我的话")
        self.assertIn("[祈祷] 不好意思，不太合适哦", theirs)
        self.assertNotIn(GREETING, theirs, "招呼语不能进 HR 语料")
        self.assertNotIn(PLACEHOLDER, theirs, "系统占位不能进 HR 语料")


if __name__ == "__main__":
    unittest.main()

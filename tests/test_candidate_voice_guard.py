"""回归：不得把「我自己说的话」当成 HR 的消息（2026-09-19 真实事故）

事故复现（原样数据，非构造）：
  09-18 我方发出：「目前离职状态，随时能到岗，一周内就行。薪资具体看岗位内容聊。…」
  09-19 扫描侧却把「目前离职状态，10日内到岗」判定为 **HR 的消息**
        → 于是自动生成并发出了一条「对着自己回答」的消息：
          「离职状态对得上，10天内到岗没问题。…」
  在 HR 眼里就是莫名其妙的重复 —— 而这条恰好发生在唯一一个正在正常互动的会话上。

为什么留痕兜不住：被误判的文本与留痕原文**不完全一致**（摘要式复述），
8 字前缀也对不上 → 只能靠「口吻识别」兜底。

本用例同时锁定**反向保护**：真 HR 的提问（哪怕也提到「到岗」「薪资」）不得被误杀。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import sync_chat_status as S  # noqa: E402


# ── 事故原文 ──
_OURS_0918 = ("目前离职状态，随时能到岗，一周内就行。薪资具体看岗位内容聊。"
              "我做过AI客服知识库（RAG）和商品监控两个落地项目，Python加LLM集成都是自己写的")
_MISREAD_0919 = "目前离职状态，10日内到岗"          # ← 被当成 HR 说的（其实是我方口径）
_ZHOU_0918 = ("了解，人工评测为主的岗位我可以考虑。我做过RAG匹配引擎和95分球鞋监控，"
              "里面有LLM精筛、Prompt设计和结果校验，也会Python、数据管道")

# ── 真 HR 原文（反向保护）──
_REAL_HR_0918 = "好的，简历收到啦，请问目前在职吗，多久能到岗呀~期望薪资方便了解一下吗~"


class TestCandidateVoiceGuard(unittest.TestCase):

    def test_the_actual_incident_is_no_longer_treated_as_hr(self):
        """09-19 事故原文：必须判为「不是我方收到的 HR 消息」。"""
        # 前置事实：它与我方原文不相等，留痕匹配不到（这正是事故成因）
        self.assertNotEqual(_MISREAD_0919, _OURS_0918)
        self.assertFalse(S._is_hr_real_message(_MISREAD_0919, {_OURS_0918}),
                         "「目前离职状态，10日内到岗」又被当成 HR 消息了")

    def test_zhouxiansheng_incident_text(self):
        """09-18 卓越际联 周先生：同样是被回流成 HR 消息的我方原话。"""
        self.assertFalse(S._is_hr_real_message(_ZHOU_0918, set()))

    def test_our_own_sent_text(self):
        self.assertFalse(S._is_hr_real_message(_OURS_0918, {_OURS_0918}))

    def test_first_person_candidate_phrases(self):
        for m in ["目前离职状态，10日内到岗",
                  "目前离职，随时到岗",
                  "我一周内能到岗",
                  "我的期望薪资是 8-12K",
                  "我是2025届本科",
                  "我的简历已发，请查收",
                  "人工评测为主的岗位我可以考虑",
                  "我做过知识库 RAG 客服"]:
            with self.subTest(msg=m):
                self.assertFalse(S._is_hr_real_message(m, set()),
                                 f"漏判（仍会被当成 HR 消息）：{m}")


class TestRealHRMessagesSurvive(unittest.TestCase):
    """反向保护：真 HR 的提问绝不能被误杀。"""

    def test_real_hr_0918_huiboyuntong(self):
        """这条同时含「到岗」「薪资」——最容易被误杀的一条。"""
        self.assertTrue(S._is_hr_real_message(_REAL_HR_0918, set()),
                        "真 HR 消息被误杀了")

    def test_other_real_hr_messages(self):
        for m in ["你好，方便发一份简历吗？",
                  "请问您什么时候能到岗呢？",
                  "我们这边是双休，岗位职责主要是数据标注",
                  "薪资范围 8-12K，您能接受吗？",
                  "麻烦您发一份附件简历",
                  "简历收到啦，明天下午方便电话沟通吗"]:
            with self.subTest(msg=m):
                self.assertTrue(S._is_hr_real_message(m, set()),
                                f"误杀真 HR 消息：{m}")

    def test_system_placeholder_still_filtered(self):
        for m in ["您正在与Boss直聘沟通", "对方已查看您的简历"]:
            with self.subTest(msg=m):
                self.assertFalse(S._is_hr_real_message(m, set()))


if __name__ == "__main__":
    unittest.main()

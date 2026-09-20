#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scam_guard 回归测试 —— 招聘诈骗防线（2026-09-20 新增）。

用例来源：
  - 2026-09-20 人工核「讷树网络 · AI训练师（远程办公）」时确认的判据边界：
    该岗**没有任何硬信号**，只有「远程 + 标注/训练师 + 要个人电脑」这类弱信号。
    这正是本模块「只拦硬信号、弱信号只标记」的原因 —— 按组合词拦会误杀
    「万声通讯 · AI影视标注（全职居家）」这类正经居家岗。
  - 公开反诈材料里的「招转培」套路（培训后上岗 / 包就业 / 收费名目）。

⚠️ 本文件**不写任何 HR 真实姓名**（PII 红线）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scam_guard  # noqa: E402
from job_decision import evaluate_job  # noqa: E402


class TestHardFeeSignals(unittest.TestCase):
    """硬信号 A：入职前收费 → 一律拦。"""

    def test_training_fee(self):
        hit, why = scam_guard.detect_scam("某网络", "AI训练师（远程办公）",
                                          "入职需缴纳培训费 1980 元")
        self.assertTrue(hit)
        self.assertIn("诈骗红线:入职前收费", why)
        self.assertIn("培训费", why)

    def test_deposit(self):
        hit, why = scam_guard.detect_scam("某公司", "数据标注员", "需交押金 500 元")
        self.assertTrue(hit)
        self.assertIn("押金", why)

    def test_uniform_and_badge_fee(self):
        hit, why = scam_guard.detect_scam("某公司", "文员", "入职需缴纳服装费、工牌费 380 元")
        self.assertTrue(hit)
        self.assertIn("服装费", why)

    def test_software_license_fee(self):
        hit, _ = scam_guard.detect_scam("某公司", "远程标注", "需自费购买软件授权费 200/月")
        self.assertTrue(hit)

    def test_device_fee(self):
        hit, _ = scam_guard.detect_scam("某公司", "居家客服", "设备费自理，约 2000 元")
        self.assertTrue(hit)

    def test_fee_word_in_title_is_caught(self):
        # 收费词出现在**标题**上同样命中 —— 扫描是 company+title+desc 拼接后做的
        hit, why = scam_guard.detect_scam("某公司", "标注员（收报名费）", "")
        self.assertTrue(hit)
        self.assertIn("报名费", why)

    def test_clean_job_not_blocked(self):
        hit, why = scam_guard.detect_scam("某科技", "AI应用工程师",
                                          "负责 RAG 知识库搭建，双休，五险一金")
        self.assertFalse(hit)
        self.assertEqual(why, "")


class TestIllegalSignals(unittest.TestCase):
    """硬信号 B：垫付 / 刷单 / 走账 / 拉人头 → 一律拦。"""

    def test_advance_payment(self):
        hit, why = scam_guard.detect_scam("某公司", "电商运营", "需先行垫付货款，按单结算")
        self.assertTrue(hit)
        self.assertIn("诈骗红线:垫付/刷单", why)

    def test_brush_orders(self):
        hit, why = scam_guard.detect_scam("某公司", "客服", "主要做刷单")
        self.assertTrue(hit)
        self.assertIn("刷单", why)

    def test_pyramid(self):
        hit, why = scam_guard.detect_scam("某公司", "推广", "发展下线有提成")
        self.assertTrue(hit)
        self.assertIn("发展下线", why)

    def test_money_laundering(self):
        hit, _ = scam_guard.detect_scam("某公司", "财务助理", "帮忙走账，日结 300")
        self.assertTrue(hit)


class TestTrainToHire(unittest.TestCase):
    """硬信号 C：显式招转培 → 一律拦。"""

    def test_train_then_hire(self):
        hit, why = scam_guard.detect_scam("某公司", "AI训练师", "培训后上岗，薪资 12K 起")
        self.assertTrue(hit)
        self.assertIn("招转培", why)

    def test_guaranteed_job(self):
        hit, _ = scam_guard.detect_scam("某公司", "AI训练师", "签培训协议，培训包就业")
        self.assertTrue(hit)

    def test_negated_guarantee_passes(self):
        # 「不包就业」是坦白，不是套路 —— 不能被词表反过来杀掉
        hit, _ = scam_guard.detect_scam("某公司", "AI训练师", "本机构不包就业，需自行投递")
        self.assertFalse(hit)


class TestNegation(unittest.TestCase):
    """否定语境必须放行 —— 正规 JD 的「不收取任何费用」是加分项。"""

    def test_no_fee_disclaimer_passes(self):
        hit, _ = scam_guard.detect_scam("某科技", "AI应用工程师",
                                        "本公司不收取任何费用")
        self.assertFalse(hit)

    def test_company_bears_cost_passes(self):
        hit, _ = scam_guard.detect_scam("某科技", "AI应用工程师",
                                        "培训费由公司承担，无需个人支付")
        self.assertFalse(hit)

    def test_negation_does_not_cross_clause(self):
        # ★ 关键用例：逗号左边说「无需经验」，右边才是「需缴纳培训费」。
        #   不分句判就会误放行 —— 这正是必须按分句找否定的原因。
        hit, why = scam_guard.detect_scam("某公司", "数据标注",
                                          "无需经验，需缴纳培训费 800 元")
        self.assertTrue(hit)
        self.assertIn("培训费", why)

    def test_negation_does_not_cross_period(self):
        hit, _ = scam_guard.detect_scam("某公司", "文员",
                                        "无经验可投。押金 500 元，离职退还")
        self.assertTrue(hit)

    def test_negation_window_is_bounded(self):
        # 否定词在**同一分句内但离得太远**（超过窗口）→ 不算修饰关系，仍然拦。
        # 「无需工作经验」修饰的是「经验」，管不到后面的「押金」。
        hit, why = scam_guard.detect_scam("某公司", "文员",
                                          "无需工作经验就可入职本岗位需缴纳押金")
        self.assertTrue(hit)
        self.assertIn("押金", why)


class TestWeakFlags(unittest.TestCase):
    """弱信号：只标记、**不参与裁决**。"""

    def test_remote_byo_device(self):
        flags = scam_guard.weak_flags("某公司", "AI训练师（远程办公）",
                                      "请问你目前是否有个人电脑")
        self.assertIn("远程岗要求自备设备", flags)

    def test_shell_entity(self):
        flags = scam_guard.weak_flags("昆明某某网络科技工作室", "AI训练师", "")
        self.assertIn("主体形态:工作室/个人独资", flags)

    def test_remote_annotation(self):
        flags = scam_guard.weak_flags("某公司", "AI训练师（远程办公）", "")
        self.assertIn("远程+标注/训练师", flags)

    def test_zero_exp_plus_hype(self):
        flags = scam_guard.weak_flags("某公司", "AI训练师", "零基础可做，月入过万很轻松")
        self.assertIn("话术:零基础+高薪", flags)

    def test_clean_job_has_no_flags(self):
        self.assertEqual(scam_guard.weak_flags("某科技", "AI应用工程师", "RAG 知识库搭建"), [])

    def test_flags_never_block(self):
        # 弱信号全中，硬信号一个没有 → 仍然放行
        company = "昆明某某网络科技工作室"
        title = "AI训练师（远程办公）"
        desc = "居家办公，需自备电脑，零基础可做，月入过万很轻松"
        self.assertFalse(scam_guard.detect_scam(company, title, desc)[0])
        flags = scam_guard.weak_flags(company, title, desc)
        self.assertEqual(len(flags), 4)


class TestLegitRemoteNotBlocked(unittest.TestCase):
    """真实正经居家/远程岗不能被误杀（库里同类岗位 47 条，多数是正规的）。"""

    CASES = [
        ("万声通讯实业有限公司", "AI影视标注（全职居家）", "负责影视内容标注，双休，五险一金"),
        ("汉克时代", "音乐数据标注/AI训练师（居家办公）", "音乐数据处理，公司提供设备"),
        ("创焱智科", "视频描述标注员（影视/文学方向）-居家办公", "视频内容描述撰写，双休"),
        ("澳鹏科技", "AI训练师(音乐方向)", "数据标注与评测，1-3年经验，本科"),
        ("某公司", "数据标注员【居家备用电脑】", "需自备电脑，日结 150-200/天"),
    ]

    def test_none_blocked(self):
        for company, title, desc in self.CASES:
            with self.subTest(title=title):
                hit, why = scam_guard.detect_scam(company, title, desc)
                self.assertFalse(hit, f"{title} 被误拦: {why}")


class TestOriginCase(unittest.TestCase):
    """本案：讷树网络 · AI训练师（远程办公）。

    2026-09-20 人工核过 —— 该岗**没有硬信号**，只有弱信号。
    所以它不该被自动拦，而是该带着 risk_flags 进人工复核。
    这条用例锁住的是「不要为了这一个案子把弱信号升级成硬拦」。
    """

    COMPANY = "讷树网络"
    TITLE = "AI训练师（远程办公）"
    HR_LINE = "由于工作过程中需要使用电脑完成相关任务，请问你目前是否有个人电脑"

    def test_no_hard_signal(self):
        self.assertFalse(scam_guard.detect_scam(self.COMPANY, self.TITLE, self.HR_LINE)[0])

    def test_weak_flags_present(self):
        flags = scam_guard.weak_flags(self.COMPANY, self.TITLE, self.HR_LINE)
        self.assertIn("远程岗要求自备设备", flags)
        self.assertIn("远程+标注/训练师", flags)


class TestEvaluateJobIntegration(unittest.TestCase):
    """接入点：job_decision.evaluate_job —— 三个平台唯一的共同咽喉。"""

    def test_scam_job_rejected(self):
        d = evaluate_job("某网络", "AI训练师（远程办公）",
                         "入职需缴纳培训费 1980 元", "10-15K", city="成都")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("诈骗红线:入职前收费", d.reason)

    def test_scam_redline_beats_salary_and_schedule(self):
        # 薪资漂亮 + 双休，但入职要交钱 → 仍然 REJECT，且原因应当是诈骗红线
        d = evaluate_job("某网络", "AI应用工程师",
                         "双休，不加班，入职需缴纳设备押金 2000 元", "20-30K", city="深圳")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("诈骗红线", d.reason)

    def test_normal_job_still_allowed(self):
        d = evaluate_job("某科技", "AI应用工程师",
                         "负责 RAG 知识库搭建与 LLM 应用落地，双休", "15-25K", city="深圳")
        self.assertEqual(d.action, "ALLOW")
        self.assertEqual(d.risk_flags, [])

    def test_flags_attached_even_on_reject(self):
        # 弱信号要跟着 Decision 走，REJECT 的路上也要能看见
        d = evaluate_job("讷树网络", "AI训练师（远程办公）", "", "10-15K", city="成都")
        self.assertIn("远程+标注/训练师", d.risk_flags)

    def test_flags_attached_on_allow(self):
        d = evaluate_job("某科技", "AI应用工程师",
                         "远程办公，需自备电脑，负责 RAG 落地，双休", "15-25K", city="深圳")
        self.assertEqual(d.action, "ALLOW")
        self.assertIn("远程岗要求自备设备", d.risk_flags)


class TestDecisionCompat(unittest.TestCase):
    """Decision 加字段后不能破坏既有构造方式（全仓都是 action 位置 + 关键字）。"""

    def test_default_flags_empty(self):
        d = evaluate_job("某科技", "AI应用工程师", "RAG 落地，双休", "15-25K", city="深圳")
        self.assertIsInstance(d.risk_flags, list)

    def test_str_includes_flags(self):
        d = evaluate_job("讷树网络", "AI训练师（远程办公）", "", "10-15K", city="成都")
        self.assertIn("风险:", str(d))

    def test_str_without_flags_unchanged(self):
        d = evaluate_job("某科技", "AI应用工程师", "RAG 落地，双休", "15-25K", city="深圳")
        self.assertNotIn("风险:", str(d))


if __name__ == "__main__":
    unittest.main()

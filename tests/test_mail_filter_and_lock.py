"""回归：① 邮件过滤器不得静默丢弃真人 HR 信  ② 发送失败必须释放回复锁

两起都是 2026-09-23 的实名事故（不是假想）：
① 扫描脚本把「东亚银行 申请未完成提醒」「TÜV Rheinland 投递确认」静默丢弃 ——
   发件人是 do_not_reply / 系统通知样式，被判成非招聘 → 用户只能自己去邮箱里发现。
② 「张效双元」那条回复在 Boss 侧定位不到会话 → 发送失败 → 条目卡在 approved →
   release_if_empty() 永远看到「还有待审」→ 投递被 REPLY_REVIEW_LOCK 无限挂起。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reply_lock as RL  # noqa: E402
import scan_job_mail as SM  # noqa: E402

# ── 实测被丢弃的发件人（原文）──
_MUST_PASS = [
    ("请于今日完成你在东亚中国的职位",
     "东亚银行（中国）有限公司 <BEAChinaRecruitment_do_not_reply@example.com>"),
    ("Thank you for applying to TUV Rheinland",
     "TUV Rheinland@myHR <system@successfactors.example.com>"),
    ("【面试邀请】AI应用工程师",
     "前程无忧 <service@51job.example.com>"),
]
# ── 必须继续拦掉的噪声（不准因为放宽而混进来）──
_MUST_DROP = [
    "Apple Store <order_acknowledgment@email.apple.com>",
    "Apple <noreply@icloud.com.cn>",
    "Roblox no-reply <no-reply@roblox.com>",
    "Google <no-reply@accounts.google.com>",
    "Xiaomi MiMo <noreply@m.xiaomi.com>",
    "QQ邮箱团队 <10000@qq.com>",
]


class TestMailFilterDoesNotDropHR(unittest.TestCase):
    def test_real_dropped_mails_now_pass(self):
        for subject, sender in _MUST_PASS:
            with self.subTest(sender=sender):
                ok, why = SM.is_job_mail(subject, sender, "请尽快完成这些步骤，确保我们可以继续处理您的申请")
                self.assertTrue(ok, f"又被丢掉了：{sender}（why={why}）")

    def test_noise_still_dropped(self):
        for sender in _MUST_DROP:
            with self.subTest(sender=sender):
                ok, why = SM.is_job_mail("关于你的订单 安全提醒", sender, "点击查看详情")
                self.assertFalse(ok, f"噪声邮件被误放行：{sender}（why={why}）")

    def test_allowlist_takes_precedence_over_blocklist(self):
        """放行表优先于黑名单：招聘域即使与黑名单同域也不该丢。"""
        self.assertIn("RECRUIT_ALLOW", dir(SM))
        ok, why = SM.is_job_mail("申请未完成", "BEAChinaRecruitment_do_not_reply@example.com", "")
        self.assertTrue(ok)
        self.assertEqual(why, "recruit_allow")


class TestPlatformMarketingNotTreatedAsHR(unittest.TestCase):
    """平台自营 EDM 不是 HR 沟通（2026-09-25）。

    背景：放行表为防丢银行/ATS 往来信而排在前面，副作用是招聘平台的营销邮件
    也被放行 —— 实测 51job quickjobs 子域的「职位动态推送」（无 AD 标记、
    标题写「与热招的行政/后勤职位要求高度一致」）被判成招聘邮件。
    修法只拦「平台域 + 营销特征」的交集，真人 HR 与银行 ATS 一律不动。

    夹具一律合成地址（RFC 2606 保留 TLD .test），保留判定 token（品牌 + 营销特征）。
    """

    _PROMO = [
        # (说明, subject, sender)
        ("51job 营销·职位动态推送(无 AD 标记)",
         "@王先生，我们热招的行政/后勤职位与您的经历高度一致",
         "前程无忧 <service@quickjobs.51job.test>"),
        ("51job 营销·EDM 通道路由",
         "王小满，被邀请参加AI证书培训，可领政府补贴",
         "前程无忧(51Job) <mkt@51job.test>"),
    ]

    def test_platform_promo_mails_are_dropped(self):
        for desc, subject, sender in self._PROMO:
            with self.subTest(desc=desc):
                ok, why = SM.is_job_mail(subject, sender, "邀您投递简历，职位急招中！立即查看。")
                self.assertFalse(ok, f"平台营销邮件被当成招聘放行：{desc}（why={why}）")

    def test_platform_real_recruitment_still_passes(self):
        """反向保护：平台发的真招聘通知不能因为加这道闸被拦。"""
        for subject, sender in [
            ("【面试邀请】AI应用工程师", "前程无忧 <hr@51job.test>"),
            ("您的简历已被查看", "BOSS直聘 <jobs@zhipin.test>"),
        ]:
            with self.subTest(sender=sender):
                ok, why = SM.is_job_mail(subject, sender, "请尽快回复确认面试时间")
                self.assertTrue(ok, f"平台真招聘通知被误拦：{sender}（why={why}）")

    def test_bank_marketing_tradeoff_unchanged(self):
        """已知取舍（原样保留）：银行/ATS 的营销信仍会放行。

        这不是期望行为，而是"宁可偶有误放，也不能再丢真人 HR 信"的既定取舍。
        用断言钉住它，防止将来有人为了"更干净"把放行表挪到后面 —— 那会重新
        丢东亚银行那类 do_not_reply 的申请提醒。
        """
        ok, why = SM.is_job_mail("东亚中国 您的专属服务权益更新", "东亚银行 <marketing@bank.test>", "")
        self.assertTrue(ok)
        self.assertEqual(why, "recruit_allow")


class TestUrgentDeadlineMails(unittest.TestCase):
    """有截止时间的申请动作项必须被单独识别（比面试邀请更急，过期即作废）。"""

    def test_real_deadline_subjects_are_urgent(self):
        for s in ["请于今日完成你在东亚中国的职位",
                  "请于今日完成你在汇丰集团的职位",
                  "您的申请尚未完成，请尽快完善",
                  "请继续完成您的申请"]:
            with self.subTest(subject=s):
                self.assertTrue(SM.is_urgent_mail(s), f"没识别成紧急：{s}")

    def test_normal_mails_not_urgent(self):
        for s in ["【面试邀请】AI应用工程师",
                  "简历收到啦，请问方便电话沟通吗",
                  "Thank you for applying to TUV Rheinland",
                  "您的意见很重要 - 应聘体验"]:
            with self.subTest(subject=s):
                self.assertFalse(SM.is_urgent_mail(s), f"误判成紧急：{s}")


class TestSendFailedReleasesLock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._old = (RL.PENDING_FILE, RL.LOCK_FILE)
        RL.PENDING_FILE = d / "reply_pending.json"
        RL.LOCK_FILE = d / "reply_review.lock"
        RL.PENDING_FILE.write_text(json.dumps([
            {"id": "R1", "company": "A公司", "status": "approved"},
            {"id": "R2", "company": "B公司", "status": "pending"},
            {"id": "R3", "company": "C公司", "status": "sent"},
        ], ensure_ascii=False), encoding="utf-8")
        RL.LOCK_FILE.write_text('{"reason": "HR会话待人工审核"}', encoding="utf-8")

    def tearDown(self):
        RL.PENDING_FILE, RL.LOCK_FILE = self._old
        self._tmp.cleanup()

    def test_failed_send_becomes_terminal_and_releases_lock(self):
        self.assertTrue(RL.mark_send_failed("R1", "找不到会话"))
        data = json.loads(RL.PENDING_FILE.read_text(encoding="utf-8"))
        r1 = next(s for s in data if s["id"] == "R1")
        self.assertEqual(r1["status"], "send_failed")
        self.assertEqual(r1["send_failed_reason"], "找不到会话")
        self.assertIn("send_failed_at", r1)
        # 还有一条真 pending → 锁必须留着
        self.assertFalse(RL.release_if_empty())
        self.assertTrue(RL.is_locked())
        # 清掉真 pending 后 → 锁释放
        RL.mark_send_failed("R2", "同上")
        self.assertTrue(RL.release_if_empty())
        self.assertFalse(RL.is_locked())

    def test_sent_items_untouched(self):
        self.assertFalse(RL.mark_send_failed("R3", "不该动"))
        data = json.loads(RL.PENDING_FILE.read_text(encoding="utf-8"))
        self.assertEqual(next(s for s in data if s["id"] == "R3")["status"], "sent")

    def test_unknown_id_is_noop(self):
        self.assertFalse(RL.mark_send_failed("NOPE", "x"))


if __name__ == "__main__":
    unittest.main()

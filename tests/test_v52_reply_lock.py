#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5.2 REPLY_REVIEW_LOCK tests (Rule 12: reply priority > auto-apply)."""
import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class TestReplyLock(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="replylock_"))
        os.environ["REPLY_LOCK_FAKE_SEND"] = "1"
        import reply_lock
        self.rl = importlib.reload(reply_lock)
        self.rl.DATA_DIR = self.tmp
        self.rl.LOCK_FILE = self.tmp / "reply_review.lock"
        self.rl.PENDING_FILE = self.tmp / "reply_pending.json"
        self.rl.CHECKPOINT_FILE = self.tmp / "queue_checkpoint.json"
        self.rl.STATS_FILE = self.tmp / "reply_stats.json"

    def tearDown(self):
        os.environ.pop("REPLY_LOCK_FAKE_SEND", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── Confirmation semantics (the critical safety test) ──

    def test_only_explicit_confirmations_pass(self):
        for phrase in ["发送", "确认发送", "可以发", "发吧", "确认", "同意发送", "发出去"]:
            self.assertTrue(self.rl.is_confirmation(phrase), f"should confirm: {phrase}")

    def test_ambiguous_phrases_never_confirm(self):
        # The user's exact anti-list — "可以"/"行" must NOT authorize a send
        for phrase in ["嗯", "看看", "可以", "还行", "行", "没问题吧", "应该可以", "好", "ok"]:
            self.assertFalse(self.rl.is_confirmation(phrase), f"must NOT confirm: {phrase}")

    def test_trailing_punctuation_normalized(self):
        self.assertTrue(self.rl.is_confirmation("确认发送。"))
        self.assertFalse(self.rl.is_confirmation("发吧？我觉得再看看"))

    # ── Lock lifecycle ──

    def _sessions(self):
        return [{"id": "R1", "company": "某某科技", "hr_name": "陈女士",
                 "job": "AI应用工程师", "hr_message": "有Dify项目经验吗？",
                 "draft": "有的，我近期用Dify搭过RAG知识库工作流。",
                 "purpose": "回应HR技术问题", "status": "pending"}]

    def test_acquire_locks_and_pauses_apply(self):
        self.assertFalse(self.rl.is_locked())
        self.rl.acquire(self._sessions())
        self.assertTrue(self.rl.is_locked(), "pending reply must block auto-apply")

    def test_unapproved_send_is_refused(self):
        self.rl.acquire(self._sessions())
        ok, msg = self.rl.send_approved("R1")
        self.assertFalse(ok, "sending without explicit confirm must fail")
        self.assertIn("禁止发送", msg)

    def test_confirm_then_send_then_release(self):
        self.rl.acquire(self._sessions())
        ok, _ = self.rl.approve("R1", "嗯")
        self.assertFalse(ok, "ambiguous phrase must not approve")
        ok, _ = self.rl.approve("R1", "确认发送")
        self.assertTrue(ok)
        ok, msg = self.rl.send_approved("R1")
        self.assertTrue(ok, msg)
        self.assertFalse(self.rl.is_locked(), "queue empty → lock must release")

    def test_reject_never_sends_and_releases(self):
        self.rl.acquire(self._sessions())
        ok, _ = self.rl.reject("R1", "不想回这家")
        self.assertTrue(ok)
        self.assertFalse(self.rl.is_locked())
        ok, msg = self.rl.send_approved("R1")
        self.assertFalse(ok, "rejected item must never be sendable")

    def test_edit_resets_to_pending(self):
        self.rl.acquire(self._sessions())
        self.rl.approve("R1", "确认")
        self.rl.edit_draft("R1", "改过的说法")
        item = [s for s in self.rl.pending() if s["id"] == "R1"][0]
        self.assertEqual(item["status"], "pending", "edited draft must re-enter review")
        ok, _ = self.rl.send_approved("R1")
        self.assertFalse(ok, "edit ≠ approval; must not send")

    # ── Checkpoint (no re-init, no duplicate quota spend) ──

    def test_checkpoint_roundtrip(self):
        self.rl.save_checkpoint({"done_combos": ["深圳×AI应用工程师", "深圳×AI实施"],
                                 "stopped_at": "广州×AI解决方案"})
        cp = self.rl.load_checkpoint()
        self.assertEqual(cp["stopped_at"], "广州×AI解决方案")
        self.rl.clear_checkpoint()
        self.assertEqual(self.rl.load_checkpoint(), {})

    def test_duplicate_acquire_merges_queue(self):
        self.rl.acquire(self._sessions())
        self.rl.acquire([{"id": "R2", "company": "另一家", "hr_name": "陈男士",
                          "hr_message": "到岗时间？", "draft": "一个月内",
                          "purpose": "问到岗时间", "status": "pending"}])
        ids = [s["id"] for s in self.rl.pending()]
        self.assertEqual(sorted(ids), ["R1", "R2"])

    # ── Dual-track stats (reply counts never touch apply quota) ──

    def test_stats_are_independent(self):
        self.rl.acquire(self._sessions())
        st = self.rl.stats()
        self.assertGreaterEqual(st["hr_messages_detected"], 1)
        self.assertGreaterEqual(st["hr_replies_drafted"], 1)
        for k in st:
            self.assertTrue(k.startswith("hr_") or k == "updated_at",
                            "reply stats must be a separate track")

    # ── boss_apply wiring (the file the subagent warned about) ──

    def test_boss_apply_checks_lock_before_each_task(self):
        src = (Path(__file__).resolve().parent.parent / "boss_apply.py").read_text(encoding="utf-8")
        self.assertIn("import reply_lock", src)
        self.assertIn("reply_lock.is_locked()", src)
        # lock halt must stop the outer city loop too (no resume-into-work-queue bug)
        self.assertIn("_lock_halt", src)
        # checkpoint advance after each successful combo
        self.assertIn("reply_lock.save_checkpoint", src)
        # normal completion clears checkpoint; lock-halt preserves it
        self.assertIn("reply_lock.clear_checkpoint()", src)
        # the halt break must live INSIDE the keyword loop before the send call
        ki = src.index("reply_lock.is_locked()")
        ri = src.index("a, s, f = run_single_cycle(")
        self.assertLess(ki, ri, "lock check must gate before any send attempt")

    def test_hr_auto_reply_no_longer_sends(self):
        src = (Path(__file__).resolve().parent.parent / "hr_auto_reply.py").read_text(encoding="utf-8")
        # main() must route drafts into reply_lock queue, not send_safely
        self.assertIn("reply_lock", src)
        self.assertIn("_RL.acquire(sessions)", src)
        # no active send_safely invocation in main/watch paths
        self.assertNotIn("send_safely(results)", src)
        self.assertNotIn("send_safely(new_results", src)
        # --send backdoor must be refused
        self.assertIn("已按 v5 第十二条拆除", src)


if __name__ == "__main__":
    unittest.main()


class TestDuplicateAcquireBug(unittest.TestCase):
    """2026-09-05 回归：同公司同HR的第二条消息不能被 sent 旧记录挡住。"""

    def setUp(self):
        import tempfile, shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="rlbug_"))
        import reply_lock
        import importlib
        self.rl = importlib.reload(reply_lock)
        self.rl.DATA_DIR = self.tmp
        self.rl.LOCK_FILE = self.tmp / "reply_review.lock"
        self.rl.PENDING_FILE = self.tmp / "reply_pending.json"
        self.rl.STATS_FILE = self.tmp / "reply_stats.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_message_same_hr_not_blocked_by_sent(self):
        os.environ["REPLY_LOCK_FAKE_SEND"] = "1"
        s1 = {"id": "R1", "company": "卓越", "hr_name": "张女士", "status": "pending",
              "hr_message": "第一问", "draft": "答1", "purpose": "x"}
        self.rl.acquire([s1])
        self.rl.approve("R1", "确认")
        ok, _ = self.rl.send_approved("R1")
        self.assertTrue(ok)
        # 第二条同人消息
        s2 = {"id": "R2", "company": "卓越", "hr_name": "张女士", "status": "pending",
              "hr_message": "第二问", "draft": "答2", "purpose": "x"}
        self.rl.acquire([s2])
        ids = [s["id"] for s in self.rl.pending()]
        self.assertIn("R2", ids, "同HR第二条必须能入队（不被sent旧记录挡）")
        os.environ.pop("REPLY_LOCK_FAKE_SEND", None)


class TestSameHrNewMessageDedupe(unittest.TestCase):
    """2026-09-16 回归：同一 HR 的 pending 条目不能把**新消息**的草稿吞掉。

    当天慧博云通 HR 从"要附件简历"追问到"在职吗/到岗时间/期望薪资"，
    reply_lock 用 (公司, HR名) 去重 → 新草稿被静默丢弃，日志却报"已入队"。
    key 加上消息正文后：新消息入队，同一句重复扫到仍去重。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rldedupe_"))
        import reply_lock
        self.rl = importlib.reload(reply_lock)
        self.rl.DATA_DIR = self.tmp
        self.rl.LOCK_FILE = self.tmp / "reply_review.lock"
        self.rl.PENDING_FILE = self.tmp / "reply_pending.json"
        self.rl.STATS_FILE = self.tmp / "reply_stats.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _s(self, sid, msg):
        return {"id": sid, "company": "慧博云通", "hr_name": "陈先生", "status": "pending",
                "hr_message": msg, "draft": f"答{sid}", "purpose": "x"}

    def test_new_message_from_same_hr_is_queued(self):
        self.rl.acquire([self._s("R1", "我想要一份您的附件简历，您是否同意")])
        self.rl.acquire([self._s("R2", "简历收到啦，请问目前在职吗，多久能到岗呀")])
        ids = [s["id"] for s in self.rl.pending()]
        self.assertIn("R2", ids, "同HR的新消息必须入队（不能被 pending 旧条目吞掉）")
        self.assertEqual(sorted(ids), ["R1", "R2"])

    def test_same_message_rescanned_is_deduped(self):
        self.rl.acquire([self._s("R1", "同一句话")])
        self.rl.acquire([self._s("R1b", "同一句话")])   # 下一轮扫描重复扫到
        ids = [s["id"] for s in self.rl.pending()]
        self.assertEqual(ids, ["R1"], "同一句重复扫到不得重复入队")


class TestNoPhantomLock(unittest.TestCase):
    """2026-09-19 回归：全部被历史去重挡住时，不得留下「有锁但 0 条待审」的幽灵锁。

    当天 10:28 实测：文女士维音那条已被判「婉拒不回」（status=rejected）的旧拒信
    被重复扫到，acquire() 去重后队列无新增，却照样写了锁 → status 显示
    「REPLY_REVIEW_LOCK 生效 / 待审核 0 条」，review 里又没条目可审，
    后续每轮 boss/51job 都会被 reply_block 挡死且无法解开。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rlphantom_"))
        import reply_lock
        self.rl = importlib.reload(reply_lock)
        self.rl.DATA_DIR = self.tmp
        self.rl.LOCK_FILE = self.tmp / "reply_review.lock"
        self.rl.PENDING_FILE = self.tmp / "reply_pending.json"
        self.rl.STATS_FILE = self.tmp / "reply_stats.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _s(self, sid, msg):
        return {"id": sid, "company": "维音中国", "hr_name": "文女士", "status": "pending",
                "hr_message": msg, "draft": "好的，收到，祝顺利！", "purpose": "礼貌收尾"}

    def test_already_rejected_message_rescan_leaves_no_lock(self):
        self.rl.acquire([self._s("R1", "感谢您的关注，但不太匹配")])
        self.assertTrue(self.rl.is_locked())
        self.rl.reject("R1", "婉拒类，用户决定不回复")
        self.assertFalse(self.rl.is_locked())
        # 下一轮扫描又扫到同一条旧消息
        self.rl.acquire([self._s("R2", "感谢您的关注，但不太匹配")])
        self.assertFalse(self.rl.is_locked(),
                         "重复扫到已处理的旧消息不得留下幽灵锁（队列空 ⇔ 无锁）")
        self.assertEqual([s["id"] for s in self.rl.pending()], ["R1"])

    def test_new_pending_message_still_locks(self):
        self.rl.acquire([self._s("R1", "第一条")])
        self.rl.reject("R1", "不回")
        self.rl.acquire([self._s("R2", "全新的问题：到岗时间？")])
        self.assertTrue(self.rl.is_locked(), "真有新待审条目时必须照旧上锁")

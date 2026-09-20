#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-09-20「规则收敛与决策链加固」新增回归用例（任务书 §4）。

分类靠类属性打标，供 tests/run_regression.py 出 §5.1 的分类看板：
    JH_CATEGORY = <分类 key>     → 看板上的哪一行
    JH_NEW = True                → 计入「新增回归」，否则计入「历史综合回归」

这些用例**只跑纯逻辑**：不连浏览器、不开 tab、不发任何网络请求（§8.1）。
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

import job_decision as JD            # noqa: E402
import schedule_inquiry as SI        # noqa: E402
import self_sent as SS               # noqa: E402


def _with_line(line=None):
    """临时设置/清除 JH_LINE，返回一个可 with 的上下文。"""
    class _Ctx:
        def __enter__(self):
            self._old = os.environ.get("JH_LINE")
            if line is None:
                os.environ.pop("JH_LINE", None)
            else:
                os.environ["JH_LINE"] = line

        def __exit__(self, *a):
            if self._old is None:
                os.environ.pop("JH_LINE", None)
            else:
                os.environ["JH_LINE"] = self._old
            return False
    return _Ctx()


# ══════════════════════════════════════════════════════════════
#  一、届别闸（§3.2）
# ══════════════════════════════════════════════════════════════

class TestCohortGate(unittest.TestCase):
    JH_CATEGORY = "cohort"
    JH_NEW = True

    def test_future_cohort_rejected(self):
        """动态年份：2027届及以后一律拦（禁止硬编码静态年份）。"""
        for title in ("2027届本/硕应届毕业生", "27届校招生", "2028届毕业生", "35届"):
            self.assertIsNotNone(JD.cohort_block_reason(title), f"{title} 应被届别闸拦")

    def test_allow_words_take_precedence_over_the_year_regex(self):
        """优先级如实记录：§3.2 把 ALLOW 定义成「解除届别阻断」，所以它连年份一并解除。

        「2031届应届生」因此**不**被届别闸拦 —— 这是任务书字面语义（ALLOW 解除阻断），
        不是实现漏洞。真正兜底的是排在最前面的身份词（校招/实习/管培生），
        校招语境的实际写法基本都会被那一层拦下（见下一个用例）。
        """
        self.assertIsNone(JD.cohort_block_reason("2031届应届生"))
        self.assertIsNotNone(JD.cohort_block_reason("2031届校园招聘"))

    def test_campus_and_intern_rejected(self):
        for title in ("校园招聘管培生", "实习生", "2027校园招聘", "AI实习生", "培训生计划"):
            self.assertIsNotNone(JD.cohort_block_reason(title), f"{title} 应被届别闸拦")

    def test_own_cohort_not_blocked(self):
        """自检：用户是 2025 届、两年择业期内 —— 25/2025/26届 都不许被拦。"""
        for title in ("25届毕业生", "2025届本科", "26届", "2026届", "两年内毕业生"):
            self.assertIsNone(JD.cohort_block_reason(title), f"{title} 不该被届别闸拦")

    def test_allow_words_lift_only_the_cohort_rule(self):
        for title in ("经验不限 应届生可投", "1年以内经验", "学生可投",
                      "往届毕业生可投", "0-1年经验", "应届生"):
            self.assertIsNone(JD.cohort_block_reason(title), f"{title} 不该被届别闸拦")

    def test_allow_words_do_not_lift_salary_gate(self):
        """任务书 §3.2 逻辑约束：ALLOW 只解除届别，后续闸门照常生效。"""
        d = JD.evaluate_job("某公司", "应届生可投 AI应用工程师", "", "3-4K", city="深圳")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("<5K", d.reason)

    def test_gate_wired_into_evaluate_job(self):
        d = JD.evaluate_job("某公司", "2027届AI应用工程师", "双休", "12-20K")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("届别闸", d.reason)


# ══════════════════════════════════════════════════════════════
#  二、兼职 / 非全日制过滤（§3.3）
# ══════════════════════════════════════════════════════════════

class TestPartTimeGate(unittest.TestCase):
    JH_CATEGORY = "part_time"
    JH_NEW = True

    # ── 放行：否定词紧贴命中词左侧 ──
    def test_negated_part_time_passes(self):
        for text in ("拒绝兼职", "非兼职", "严禁兼职", "不接受兼职", "全职（非日结）",
                     "不招兼职"):
            self.assertFalse(JD.is_part_time_job(text), f"{text} 应放行")

    # ── 拦截：任务书 §4 点名的全部反例 ──
    def test_part_time_rejected(self):
        for text in ("招聘兼职运营", "短期兼职", "客服小时工", "非日结兼职", "兼职/全职",
                     "日结工", "众包任务", "无底薪提成", "临时工", "无保底"):
            self.assertTrue(JD.is_part_time_job(text), f"{text} 应拦截")

    def test_negation_window_must_be_adjacent(self):
        """关键边界：任务书原文的「前 4 字符窗口」会把「非日结兼职」放行 —— 错。

        「非」修饰的是「日结」而不是「兼职」，整条仍是兼职岗（§4 要求 REJECT）。
        """
        self.assertTrue(JD.is_part_time_job("非日结兼职"))
        self.assertFalse(JD.is_part_time_job("非兼职"))

    def test_empty_jd_only_scans_title(self):
        """jd 为空时只能看 title，不得假装扫过正文。"""
        self.assertFalse(JD.is_part_time_job("AI应用工程师", ""))
        self.assertTrue(JD.is_part_time_job("AI应用工程师", "本岗位为兼职，按单计酬"))

    def test_gate_wired_into_evaluate_job(self):
        d = JD.evaluate_job("某公司", "招聘兼职AI运营", "双休", "12-20K")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("兼职", d.reason)


# ══════════════════════════════════════════════════════════════
#  三、薪资上限（§4「30K 上限」；本仓库 2026-09-19 用户改稿为 60K）
# ══════════════════════════════════════════════════════════════

class TestSalaryCeilingGate(unittest.TestCase):
    JH_CATEGORY = "salary_ceiling"
    JH_NEW = True

    def test_ceiling_is_the_configured_value(self):
        """口径确认：2026-09-19 用户把天花板从 30K 改到 60K（详见常量上方注释）。"""
        self.assertEqual(JD.SALARY_CEILING, 60.0)

    def test_boundary_passes_and_above_rejects(self):
        """红线语义 = 区间上限或单值 **>** 天花板。边界值本身不拦。"""
        c = int(JD.SALARY_CEILING)
        for salary in (f"20-{c}K", f"{c}K"):
            d = JD.evaluate_job("某公司", "AI应用工程师", "双休", salary, city="深圳")
            self.assertEqual(d.action, "ALLOW", f"{salary} 正好在天花板上，不该拦")
        for salary in (f"20-{c + 20}K", f"{c + 1}"):
            d = JD.evaluate_job("某公司", "AI应用工程师", "双休", salary, city="深圳")
            self.assertEqual(d.action, "REJECT", f"{salary} 超天花板，必须拦")

    def test_single_value_above_ceiling_rejected(self):
        d = JD.evaluate_job("某公司", "AI专家", "", str(int(JD.SALARY_CEILING) + 1))
        self.assertEqual(d.action, "REJECT")
        self.assertIn("K红线", d.reason)


# ══════════════════════════════════════════════════════════════
#  四、制度问询状态机（§3.1 后置兜底）
# ══════════════════════════════════════════════════════════════

class TestScheduleInquiryTriggers(unittest.TestCase):
    """OR 逻辑：任一命中即触发；四类「严禁触发」场景命中即不得触发。"""

    JH_CATEGORY = "schedule_inquiry"
    JH_NEW = True

    def test_asks_for_resume_triggers(self):
        for msg in ("方便发一份简历过来看看吗？", "麻烦把附件简历发我一下",
                    "简历发我，我先看下"):
            self.assertTrue(SI.is_substantive(msg), f"「{msg}」应触发问询")

    def test_asks_for_onboarding_time_triggers(self):
        for msg in ("最快什么时候能到岗？", "你现在是在职吗？",
                    "方便说下离职时间和到岗时间吗？"):
            self.assertTrue(SI.is_substantive(msg), f"「{msg}」应触发问询")

    def test_interview_invite_triggers(self):
        for msg in ("方便的话我们约个面试时间", "明天下午能来公司聊聊吗",
                    "先电话沟通一下，你看什么时候方便"):
            self.assertTrue(SI.is_substantive(msg), f"「{msg}」应触发问询")

    def test_substantive_text_reply_triggers(self):
        self.assertTrue(SI.is_substantive("我们这边主要做企业级RAG，你之前项目经验是怎样的？"))

    # ── 严禁触发 ──
    def test_first_greeting_does_not_trigger(self):
        for msg in ("你好", "您好", "在吗", "您好！", "哈喽~"):
            self.assertFalse(SI.is_substantive(msg), f"首句打招呼「{msg}」不该触发")

    def test_auto_greeting_does_not_trigger(self):
        """Boss 的 HR 端自动招呼：既像打招呼又要简历，但它是一句群发模板。"""
        for msg in ("您好，我是XX公司的HR，方便发份简历吗？",
                    "你好，看到您的简历很匹配我们的岗位，方便聊聊吗"):
            self.assertFalse(SI.is_substantive(msg), f"系统自动招呼「{msg}」不该触发")

    def test_job_recommendation_push_does_not_trigger(self):
        self.assertFalse(SI.is_substantive("为你推荐：AI应用工程师 15-25K，点击查看"))
        self.assertFalse(SI.is_substantive("根据您的简历为您推荐了 3 个岗位"))

    def test_read_but_unreplied_does_not_trigger(self):
        self.assertFalse(SI.is_substantive(""))
        self.assertFalse(SI.is_substantive("   "))
        self.assertFalse(SI.is_substantive("已读"))

    def test_system_placeholder_does_not_trigger(self):
        self.assertFalse(SI.is_substantive("您正在与Boss某某沟通"))

    def test_rejection_does_not_trigger(self):
        """HR 说不合适时，不该反过来问人家部门作息。"""
        self.assertFalse(SI.is_substantive("不好意思，您的经历和我们岗位不太匹配"))
        self.assertFalse(SI.is_substantive("感谢关注，该岗位已招满"))

    def test_inquiry_text_has_no_politeness_filler(self):
        for bad in ("您好", "感谢您", "希望能有机会", "期待与您"):
            self.assertNotIn(bad, SI.INQUIRY_TEXT)
        self.assertIn("固定双休还是排班轮休", SI.INQUIRY_TEXT)


class TestScheduleInquiryPersistence(unittest.TestCase):
    """只问一次 + 真落盘（§10 第 11 项：连跑两次不重复问）。"""

    JH_CATEGORY = "schedule_inquiry"
    JH_NEW = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        for name, val in (("DATA_DIR", tmp), ("STATE_FILE", tmp / "asked_state.json")):
            p = mock.patch.object(SI, name, val)
            p.start()
            self.addCleanup(p.stop)

    def test_ask_once_then_never_again(self):
        first = SI.ask_once("某科技", "HR王", session_id="R1")
        self.assertEqual(first, SI.INQUIRY_TEXT)
        # 第二轮扫描：同一 HR 换了条新消息、新会话 id —— 仍不许再问
        self.assertEqual(SI.ask_once("某科技", "HR王", session_id="R2"), "")
        self.assertTrue(SI.STATE_FILE.exists(), "已问状态必须真的落盘")
        data = json.loads(SI.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(list(data["asked"].keys()), ["某科技|HR王"])

    def test_state_survives_reload(self):
        SI.ask_once("某科技", "HR王")
        self.assertTrue(SI.has_asked("某科技", "HR王"))
        self.assertFalse(SI.has_asked("另一家", "HR王"))
        self.assertFalse(SI.has_asked("某科技", "HR李"))

    def test_different_hr_gets_its_own_question(self):
        SI.ask_once("某科技", "HR王")
        self.assertEqual(SI.ask_once("某科技", "HR李"), SI.INQUIRY_TEXT)

    def test_two_scan_rounds_ask_exactly_once(self):
        """模拟连跑两轮扫描：草稿里问询句只能出现一次。"""
        msgs = [{"company": "某科技", "name": "HR王", "message": "方便发一份简历吗？"}]

        def one_round():
            drafts = []
            for i, m in enumerate(msgs):
                draft = "简历已发。"
                if SI.is_substantive(m["message"]):
                    q = SI.ask_once(m["company"], m["name"], session_id=f"R{i}")
                    if q:
                        draft = f"{draft}\n{q}"
                drafts.append(draft)
            return drafts

        r1, r2 = one_round(), one_round()
        self.assertIn(SI.INQUIRY_TEXT, r1[0])
        self.assertNotIn(SI.INQUIRY_TEXT, r2[0], "第二轮不许再问同一个 HR")


# ══════════════════════════════════════════════════════════════
#  五、双线隔离（AI 线 / 过渡线）
# ══════════════════════════════════════════════════════════════

class TestLineIsolation(unittest.TestCase):
    JH_CATEGORY = "line_isolation"
    JH_NEW = True

    def test_same_job_two_verdicts(self):
        """同岗位：AI 线按底薪口径拒，过渡线 ≥4K 放行上岸。"""
        job = ("某科技", "AI应用工程师", "负责企业AI落地", "6-8K")
        with _with_line(None):
            ai = JD.evaluate_job(*job, city="深圳")
        with _with_line("transition"):
            tr = JD.evaluate_job(*job, city="深圳")
        self.assertEqual(ai.action, "REJECT")
        self.assertIn("底薪口径不达标", ai.reason)
        self.assertEqual((tr.action, tr.priority), ("ALLOW", "LOW"))

    def test_floors_differ_by_line(self):
        job = ("某公司", "内勤文员", "整理档案", "4-5K")
        with _with_line(None):
            self.assertEqual(JD.evaluate_job(*job).action, "REJECT")   # AI 线硬底 5K
        with _with_line("transition"):
            self.assertEqual(JD.evaluate_job(*job).action, "ALLOW")    # 过渡线 4K
        # 两条线共同的硬底：3.5K 单值谁都不放
        with _with_line("transition"):
            self.assertEqual(JD.evaluate_job("某公司", "内勤文员", "", "3.5K").action,
                             "REJECT")

    def test_piece_rate_rejected_on_transition_line(self):
        with _with_line("transition"):
            for desc in ("计件工资，多劳多得", "按单计酬", "无底薪，多劳多得"):
                d = JD.evaluate_job("某公司", "内容审核", desc, "5-7K")
                self.assertEqual(d.action, "REJECT", f"过渡线计件岗应拒：{desc}")

    def test_ai_line_not_polluted_by_transition_env(self):
        """JH_LINE=transition 时，AI 线的薪资门槛不许被带跑。"""
        with _with_line("transition"):
            # 5-8K 在 AI 线仍要底薪声明或特批信号；过渡线才直接放行
            d = JD.evaluate_job("某外包", "采购专员", "跟单", "6-8K")
            self.assertEqual(d.action, "ALLOW")   # 过渡线语义（env 生效）
        with _with_line(None):
            d = JD.evaluate_job("某外包", "采购专员", "跟单", "6-8K")
            self.assertEqual(d.action, "REJECT")  # 回到 AI 线语义

    def test_workday_redline_shared_by_both_lines(self):
        for line in (None, "transition"):
            with _with_line(line):
                d = JD.evaluate_job("某公司", "AI应用工程师", "大小周", "12-20K")
                self.assertEqual(d.action, "REJECT", "制度红线两条线都必须生效")


# ══════════════════════════════════════════════════════════════
#  六、技术与项目措辞（§3.4）
# ══════════════════════════════════════════════════════════════

class TestWordingConsistency(unittest.TestCase):
    JH_CATEGORY = "wording"
    JH_NEW = True

    UNIFIED = "电商商品上新监控（私有接口签名对接 + 多模态 LLM 视觉识别）"

    def test_no_reverse_engineering_wording(self):
        """进仓库的文件里不得再出现「逆向」。

        范围 = git **已跟踪**的文件（= 真正公开的内容）。`archive/` 是归档、
        `*-log.json` 之类是运行日志（抓下来的平台原文，不是我们的措辞），都不在管辖内。

        排除本文件自己：它必须逐字写上这个词才能当搜索键，否则一进 index 就被自己扫中
        （自指误报，不是真命中）。其余每一个已跟踪文件照扫不误。
        """
        import subprocess
        try:
            out = subprocess.run(["git", "ls-files"], cwd=str(BASE), capture_output=True,
                                 text=True, timeout=30)
            files = [BASE / f for f in out.stdout.splitlines() if f]
        except Exception:
            files = [p for p in BASE.rglob("*") if p.is_file()]
        self_path = Path(__file__).resolve()
        hits = []
        for p in files:
            if not p.is_file() or "archive" in p.parts or ".git" in p.parts:
                continue
            if p.resolve() == self_path:      # 判据自身，见上方说明
                continue
            if p.suffix not in (".py", ".md", ".sh", ".txt", ".json", ".yml", ".yaml"):
                continue
            try:
                if "逆向" in p.read_text(encoding="utf-8", errors="ignore"):
                    hits.append(str(p.relative_to(BASE)))
            except Exception:
                continue
        self.assertEqual(hits, [], f"这些文件仍在用「逆向」措辞：{hits}")

    def test_greeting_sources_use_no_politeness_filler(self):
        import shared
        import boss_apply
        text = shared.DEFAULT_CONFIG["greeting"]
        for bad in ("您好", "感谢您", "希望能有机会", "已阅读岗位需求", "熟练掌握"):
            self.assertNotIn(bad, text, f"shared 默认招呼语不得含「{bad}」")
        for style, cfg in boss_apply.GREETING_STYLES.items():
            for bad in ("您好", "感谢您", "希望能有机会", "已阅读岗位需求", "熟练掌握"):
                self.assertNotIn(bad, cfg["pattern"], f"{style} 模板不得含「{bad}」")

    def test_default_profile_uses_unified_project_name(self):
        import hr_auto_reply
        self.assertIn("电商商品上新监控（私有接口签名对接 + 多模态 LLM 视觉识别）",
                      hr_auto_reply.DEFAULT_PROFILE)
        self.assertNotIn("逆向", hr_auto_reply.DEFAULT_PROFILE)

    def test_no_third_wording_variant_in_prompts(self):
        """不许出现第三种写法：主要变体必须归到统一口径。"""
        import hr_auto_reply
        self.assertNotIn("视觉LLM精筛", hr_auto_reply.DEFAULT_PROFILE)
        self.assertNotIn("视觉 LLM 精筛两级过滤", hr_auto_reply.DEFAULT_PROFILE)


# ══════════════════════════════════════════════════════════════
#  七、招呼语（§3.5）
# ══════════════════════════════════════════════════════════════

class TestGreetingTemplates(unittest.TestCase):
    JH_CATEGORY = "greeting"
    JH_NEW = True

    def _greetings(self):
        import boss_apply
        out = []
        for company in ("甲公司", "乙公司", "丙公司", "丁公司", "戊公司", "己公司"):
            for title, desc in (("AI应用工程师", "负责大模型应用落地"),
                                ("采购工程师", "负责IT设备采购"),
                                ("Python后端", "熟悉Django")):
                out.append(boss_apply.generate_greeting_with_meta(title, desc, company)[0])
        return out

    def test_no_banned_phrases(self):
        for g in self._greetings():
            for bad in ("您好", "感谢您", "希望能有机会", "熟练掌握", "已阅读岗位需求"):
                self.assertNotIn(bad, g, f"招呼语不得含「{bad}」：{g}")

    def test_stays_within_one_screen_and_keeps_the_question(self):
        for g in self._greetings():
            self.assertLessEqual(len(g), 120, f"招呼语超长：{g}")
            self.assertIn("？", g, f"招呼语必须问回对方：{g}")

    def test_keeps_real_background_and_role_question(self):
        import boss_apply
        g, tid = boss_apply.generate_greeting_with_meta("Python开发", "会python脚本", "测试C")
        self.assertIn(boss_apply.USER_BG["Python"], g)
        self.assertIn(boss_apply.ROLE_QUESTIONS["Python"], g)
        self.assertTrue(tid.endswith(":Python"))


class TestSelfSentRecognition(unittest.TestCase):
    """§3.5 的隐藏耦合：新招呼语发出后必须仍被判定为**我方消息**。

    这个用例不只断言字符串匹配 —— 它把「误判 → 误锁」这条因果链整个跑一遍：
    误判成 HR 消息 → 入待回复队列 → 上 REPLY_REVIEW_LOCK → 投递被挂起。
    """

    JH_CATEGORY = "greeting"
    JH_NEW = True

    def setUp(self):
        import boss_apply
        import hr_auto_reply
        import reply_lock
        self.boss_apply = boss_apply
        self.har = hr_auto_reply
        self.RL = reply_lock
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        for mod, name, val in ((SS, "DATA_DIR", tmp),
                               (SS, "SENT_FILE", tmp / "self_sent.json"),
                               (self.RL, "DATA_DIR", tmp),
                               (self.RL, "LOCK_FILE", tmp / "reply_review.lock"),
                               (self.RL, "PENDING_FILE", tmp / "reply_pending.json"),
                               # 统计/断点文件也必须隔离：否则用例会往真实的 data/ 里写
                               (self.RL, "STATS_FILE", tmp / "reply_stats.json"),
                               (self.RL, "CHECKPOINT_FILE", tmp / "queue_checkpoint.json")):
            p = mock.patch.object(mod, name, val)
            p.start()
            self.addCleanup(p.stop)
        # 留痕的"既有来源"（reply_lock / sent_replies 目录）在本机是真数据，
        # 用例必须自洽，所以清空；同时清掉 hr_auto_reply 的进程内缓存。
        for p in (mock.patch.object(SS, "_other_sources", return_value=set()),
                  mock.patch.object(self.har, "_SELF_SENT_POOL", None)):
            p.start()
            self.addCleanup(p.stop)
        self.greeting = boss_apply.generate_greeting_with_meta(
            "AI应用工程师", "负责大模型应用落地", "某科技")[0]

    def _would_be_lock_triggered(self) -> bool:
        """走一遍「扫描 → 判定 → 入队 → 上锁」的过滤链，返回是否被上锁。"""
        if not self.har._is_hr_real_message(self.greeting):
            return False                      # 判成我方消息 → 根本不入队
        sessions = [{"id": "R1", "company": "某科技", "hr_name": "HR",
                     "hr_message": self.greeting, "draft": "…", "status": "pending"}]
        self.RL.acquire(sessions)
        return self.RL.is_locked()

    def test_new_greeting_has_no_legacy_prefix(self):
        """前提确认：新招呼语确实不含旧的判定前缀，旧逻辑必然漏判。"""
        self.assertNotIn("您好", self.greeting)
        self.assertNotIn("我是", self.greeting)

    def test_without_registry_the_greeting_would_be_misread_and_lock(self):
        """对照组：没有发送留痕时，我们自己的招呼语会被当成 HR 消息 → 误锁。

        这就是 §3.5 点名的故障；下面一个用例证明留痕把它挡住了。
        """
        self.assertTrue(self._would_be_lock_triggered(),
                        "对照组应复现「误判 → 误锁」，否则这个用例测不出东西")

    def test_registry_prevents_misread_and_lock(self):
        """正例：招呼语发送成功后留痕 → 不再被当成 HR 消息 → 不会误锁。"""
        SS.record(self.greeting, channel="boss_greeting", company="某科技")
        self.har._SELF_SENT_POOL = None       # 让缓存重新加载（模拟下一轮扫描）
        self.assertFalse(self.har._is_hr_real_message(self.greeting),
                         "有留痕时，我方招呼语不得被判成 HR 消息")
        self.assertFalse(self._would_be_lock_triggered())
        self.assertFalse(self.RL.is_locked(), "不得因为自己的招呼语把投递锁上")

    def test_registry_matching_is_copy_independent(self):
        """换任何文案都认得出 —— 这正是「不许用固定前缀」要达到的效果。"""
        for text in ("主攻 AI Agent 与自动化工作流落地（私有接口签名对接 + 本地知识库落地）。"
                     "方便发份简历给您评估吗？",
                     "做过数据自动化脚本与运营流程支持，能直接上手。方便发份简历给您评估吗？"):
            SS.record(text, channel="boss_greeting")
        for text in ("主攻 AI Agent 与自动化工作流落地（私有接口签名对接 + 本地知识库落地）。"
                     "方便发份简历给您评估吗？",
                     "做过数据自动化脚本与运营流程支持，能直接上手。方便发份简历给您评估吗？"):
            self.assertTrue(SS.is_self_sent(text))

    def test_truncated_dom_text_still_recognized(self):
        """聊天列表只取前若干字，截断后仍须认得出（前缀匹配）。"""
        SS.record(self.greeting, channel="boss_greeting")
        self.assertTrue(SS.is_self_sent(self.greeting[:30]))


class TestSyncChatStatusDirection(unittest.TestCase):
    """scripts/sync_chat_status.py 的自方消息判定（§3.5 第二处耦合）。"""

    JH_CATEGORY = "greeting"
    JH_NEW = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        for mod, name, val in ((SS, "DATA_DIR", tmp),
                               (SS, "SENT_FILE", tmp / "self_sent.json")):
            p = mock.patch.object(mod, name, val)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(SS, "_other_sources", return_value=set())
        p.start()
        self.addCleanup(p.stop)
        sys.path.insert(0, str(BASE / "scripts"))
        import sync_chat_status as S
        self.S = S

    def test_new_greeting_is_mine(self):
        import boss_apply
        g = boss_apply.generate_greeting_with_meta("AI应用工程师", "职责", "某科技")[0]
        SS.record(g, channel="boss_greeting")
        self.assertTrue(self.S._is_my_message(g, set()))
        self.assertFalse(self.S._is_hr_real_message(g, set()))

    def test_real_hr_message_still_hr(self):
        import boss_apply
        g = boss_apply.generate_greeting_with_meta("AI应用工程师", "职责", "某科技")[0]
        SS.record(g, channel="boss_greeting")
        self.assertTrue(self.S._is_hr_real_message("方便发一份简历过来看看吗？", set()))

    def test_classify_last_message_direction(self):
        import boss_apply
        g = boss_apply.generate_greeting_with_meta("AI应用工程师", "职责", "某科技")[0]
        SS.record(g, channel="boss_greeting")
        self.assertEqual(self.S._classify_last_message(g, 3, set(), set()), "mine",
                         "带未读角标也不能把我方招呼语算成 HR 回复")


# ══════════════════════════════════════════════════════════════
#  八、频率与风控闸
# ══════════════════════════════════════════════════════════════

class TestRateGates(unittest.TestCase):
    JH_CATEGORY = "rate_gates"
    JH_NEW = True

    def test_platform_daily_limits(self):
        import platform_51job as P51
        import platform_liepin as PLP
        self.assertEqual(P51.DAILY_LIMIT, 100, "51job 日限额 100")
        self.assertEqual(PLP.DAILY_LIMIT, 20, "猎聘日限额 20")
        self.assertLessEqual(P51.HOURLY_CAP, 15, "单小时上限不得超过 15")

    def test_night_window_22_to_08(self):
        import platform_51job as P51
        from datetime import datetime as _dt

        class _FakeDT(_dt):
            _h = 0

            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 20, cls._h, 0, 0)

        with mock.patch.object(P51, "datetime", _FakeDT):
            for h, expect in ((22, True), (23, True), (0, True), (7, True),
                              (8, False), (12, False), (21, False)):
                _FakeDT._h = h
                self.assertEqual(P51.in_night_window(), expect,
                                 f"{h}:00 的夜禁判定应为 {expect}")

    def test_hourly_gate_rests_when_cap_reached(self):
        import io
        import contextlib
        import platform_51job as P51
        buf = io.StringIO()
        with mock.patch.object(P51, "hourly_applied", return_value=99), \
                mock.patch.object(P51, "HOURLY_REST_SEC", 0), \
                contextlib.redirect_stdout(buf):
            P51.hourly_gate(10)
        self.assertIn("hour_limit_reached", buf.getvalue(),
                      "本小时达上限必须走休息分支")
        buf2 = io.StringIO()
        with mock.patch.object(P51, "hourly_applied", return_value=3), \
                mock.patch.object(P51, "HOURLY_REST_SEC", 0), \
                contextlib.redirect_stdout(buf2):
            P51.hourly_gate(10)
        self.assertNotIn("hour_limit_reached", buf2.getvalue(),
                         "未达上限不得休息")

    def test_boss_quota_config_present(self):
        import shared
        cfg = shared.load_config()
        safety = cfg.get("safety", {})
        self.assertIn("normal_daily_cap", safety)
        self.assertLessEqual(int(safety.get("normal_daily_cap", 0)), 150,
                             "Boss 日限额不得超过 150")


# ══════════════════════════════════════════════════════════════
#  九、猎聘通道熔断（§3.6）
# ══════════════════════════════════════════════════════════════

class TestLiepinBypass(unittest.TestCase):
    JH_CATEGORY = "liepin"
    JH_NEW = True

    def test_flag_is_off(self):
        import platform_liepin as PLP
        self.assertFalse(PLP.LIEPIN_ENABLED)

    def test_short_circuit_is_the_first_statement_of_main(self):
        """必须在总入口前置阻断：在它之前不许 import config / 拉 Chrome / 取锁。"""
        import ast
        src = (BASE / "platform_liepin.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        main = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        first = main.body[0]
        self.assertIsInstance(first, ast.If, "main() 的第一句必须是熔断判断")
        cond_src = ast.unparse(first.test)
        self.assertIn("LIEPIN_ENABLED", cond_src)
        # 阻断块必须直接 return
        self.assertTrue(any(isinstance(s, ast.Return) for s in first.body),
                        "熔断分支必须直接 return，不能只是打印警告")
        # 后面才允许 import config
        self.assertIsInstance(main.body[1], ast.Import)

    def test_main_returns_without_touching_browser(self):
        import platform_liepin as PLP
        saved = sys.modules.pop("config", None)
        if saved is not None:
            self.addCleanup(sys.modules.__setitem__, "config", saved)
        with mock.patch("DrissionPage.ChromiumPage") as cp, \
                mock.patch.object(sys, "argv", ["platform_liepin.py"]):
            PLP.main()
        cp.assert_not_called()
        self.assertNotIn("config", sys.modules,
                         "熔断状态下不许走到 import config（更不许连 Chrome）")


# ══════════════════════════════════════════════════════════════
#  过渡线：钱少 ↔ 事少 的换取闸（2026-09-20）
# ══════════════════════════════════════════════════════════════

class TestTransitionIdleTrade(unittest.TestCase):
    """用户口径：「事少钱多离家近，总得占一个」。

    过渡线是上岸不是攒钱 —— 3-4K 不该一刀切拒。明显清闲的（看店/坐班/不加班那类）
    是「拿钱换时间」的合理交换；同样 3-4K 的销售/客服/流水线，钱少事还多，就没理由去。
    （「离家近」不在本闸内，由 value_score 的「地点通勤」维度单独计分。）
    """

    JH_CATEGORY = "transition_idle"
    JH_NEW = True

    def _ev(self, title, desc, salary):
        with _with_line("transition"):
            return JD.evaluate_job("某公司", title, desc, salary)

    # ── 门槛取的是区间**下限**，不是上限 ──

    def test_floor_uses_lower_bound_not_upper_bound(self):
        """`3-4K` 的下限是 3K，不能因为串里有「4K」就当成达标。

        旧实现另写了一个 re.search 抓数字，抓到的正是区间上界 → 门槛实际是虚的。
        """
        self.assertEqual(JD.parse_salary_low("3-4K"), 3.0)
        d = self._ev("内勤文员", "", "3-4K")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("低于", d.reason)

    def test_lower_bound_at_floor_still_passes(self):
        """`4-5K` 下限 = 4K，正好达标 → 照旧放行（既有行为不许动）。"""
        d = self._ev("内勤文员", "", "4-5K")
        self.assertEqual((d.action, d.priority), ("ALLOW", "LOW"))

    def test_single_value_below_floor_rejected(self):
        with _with_line("transition"):
            self.assertEqual(JD.evaluate_job("某公司", "内勤文员", "", "3.5K").action,
                             "REJECT")

    # ── 钱少 → 用「事少」换 ──

    def test_idle_low_pay_job_is_accepted(self):
        """KKV 那种看店/理货的清闲岗：钱少但事少 → 可投。"""
        d = self._ev("门店店员", "负责看店、整理货架，工作轻松不加班", "3-4K")
        self.assertEqual((d.action, d.priority), ("ALLOW", "LOW"))
        self.assertIn("事少", d.reason)

    def test_idle_signal_rescues_a_range_whose_low_is_below_floor(self):
        d = self._ev("数据录入", "工作简单易上手，基本不加班", "3.5-4.5K")
        self.assertEqual(d.action, "ALLOW")

    def test_low_pay_without_idle_signal_is_rejected(self):
        d = self._ev("内勤文员", "整理档案", "3-4K")
        self.assertEqual(d.action, "REJECT")

    # ── 反向否决：钱已少，事还多 → 没理由去 ──

    def test_busy_signal_vetoes_the_idle_trade(self):
        """「坐班」是低强度词，但「销售」是否决词 —— 3K+高提成的坐班销售不该放行。"""
        d = self._ev("销售顾问", "坐班销售，底薪3K+高提成", "3-4K")
        self.assertEqual(d.action, "REJECT")

    def test_busy_wins_even_when_idle_words_also_present(self):
        """同一段 JD 里既有「清闲」又有「客服」→ 否决优先。"""
        d = self._ev("客服", "工作清闲，不加班，负责接听", "3-4K")
        self.assertEqual(d.action, "REJECT")

    def test_piece_rate_low_pay_rejected(self):
        d = self._ev("理货员", "计件工资，多劳多得，工作简单", "3-4K")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("多劳多得", d.reason)

    # ── 只解锁「底薪 < 4K」这一条，别的闸不许被带跑 ──

    def test_idle_trade_does_not_bypass_redlines(self):
        """事少信号不能用来解制度红线。"""
        d = self._ev("门店店员", "工作轻松不加班，但大小周轮班", "3-4K")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("红线", d.reason)

    def test_idle_trade_is_transition_line_only(self):
        """同一岗位两条线结论不同：「钱少事少」这套交换只在过渡线成立。

        用「清闲／看店」而不是「轻松／不加班」做信号 —— 后两个词本来就在 AI 线的
        特批名单（SPECIAL_APPROVAL_SIGNALS）里，拿它们做对照会分不清是谁在放行。
        """
        job = ("某公司", "门店店员", "负责看店，清闲", "3-4K")
        with _with_line(None):
            self.assertEqual(JD.evaluate_job(*job).action, "REJECT")
        with _with_line("transition"):
            self.assertEqual(JD.evaluate_job(*job).action, "ALLOW")

    def test_job_category_words_are_not_idle_signals(self):
        """「文员/标注」是岗位类别，不等于事少 —— 光写岗位名不解锁门槛。"""
        for title in ("内勤文员", "数据标注", "资料整理"):
            d = self._ev(title, "负责日常事务", "3-4K")
            self.assertEqual(d.action, "REJECT", f"{title} 不该仅凭岗位名放行")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5.4 移植 MVP 测试（2026-09-12）

覆盖从 career-ops 移植的 6 个模块。每个模块都含 正例 / 反例 / 边界，
重点锁死 career-ops 用真实误报换来的那几条约束（它们正是最容易在移植里丢掉的）。
"""
import unittest
from datetime import date

import block_g as bg
import funnel_stats as fs
import interview_prep as ip
import liveness as lv
import provenance as pv
import repost_detect as rd


class TestProvenanceNumbers(unittest.TestCase):
    def test_normalize_equivalent_forms(self):
        # 正例：1.5万 与 15000 必须可比
        self.assertEqual(pv.normalize_number('1.5万'), '15000')
        self.assertEqual(pv.normalize_number('15,000'), '15000')
        self.assertEqual(pv.normalize_number('30000'), '30000')
        self.assertEqual(pv.normalize_number('3万'), '30000')

    def test_normalize_strips_chinese_counters(self):
        # 边界：5700条 与 5700 应可比
        self.assertEqual(pv.normalize_number('5700条'), '5700')
        self.assertEqual(pv.normalize_number('5700'), '5700')

    def test_normalize_keeps_percent(self):
        self.assertEqual(pv.normalize_number('92%'), '92%')

    def test_extract_numbers(self):
        got = pv.extract_numbers('处理了 3万+ 条数据，准确率 92%，覆盖 6 个城市')
        self.assertIn('30000', got)
        self.assertIn('92%', got)
        self.assertIn('6', got)

    def test_extract_precision_no_false_positive(self):
        # 反例：纯文字不应抽到数字
        self.assertEqual(pv.extract_numbers('纯文本没有数字'), [])


class TestProvenanceFourState(unittest.TestCase):
    CV = "负责 renovation-bot 客服系统，累计处理 3万 条对话，准确率 92%。"

    def test_existing_from_verbatim_source(self):
        st, _ = pv.classify_claim('30000', {'cv.md': self.CV})
        self.assertEqual(st, pv.EXISTING)
        st2, _ = pv.classify_claim('92%', {'cv.md': self.CV})
        self.assertEqual(st2, pv.EXISTING)

    def test_supported_when_number_absent_but_topic_matches(self):
        # 数字不在（7万），但同一条目讲的是同一件事 → supported
        st, _ = pv.classify_claim('70000', {'cv.md': self.CV},
                                  story_text='renovation-bot 客服系统 对话 处理 累计')
        self.assertIn(st, (pv.SUPPORTED, pv.DERIVED))

    def test_derived_when_no_trace(self):
        # 反例：数字与主题都无痕迹
        st, _ = pv.classify_claim('88888', {'cv.md': self.CV},
                                  story_text='量子计算 芯片 流片')
        self.assertEqual(st, pv.DERIVED)

    def test_user_cannot_confirm_is_hard_override(self):
        # ★ 核心约束：显式 user-cannot-confirm 压过一切（哪怕数字在 cv 里）
        st, why = pv.classify_claim('3万', {'cv.md': self.CV},
                                    provenance_state=pv.CANNOT)
        self.assertEqual(st, pv.CANNOT)
        self.assertIn('硬覆盖', why)

    def test_provenance_field_parsing(self):
        import tempfile, os
        text = ("### [交付] 项目A\n**Situation:** x\n**Result:** 处理 3万 条\n"
                "**Provenance:** user-cannot-confirm\n\n"
                "### [交付] 项目B\n**Result:** 处理 5万 条\n")
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write(text); p = f.name
        try:
            stories = pv.load_story_bank(p)
            self.assertEqual(len(stories), 2)
            self.assertEqual(stories[0].provenance_state, pv.CANNOT)
            self.assertIsNone(stories[1].provenance_state)   # 缺字段 → None（判 derived）
        finally:
            os.unlink(p)


class TestProvenanceGate(unittest.TestCase):
    def test_gate_blocks_unbacked_number(self):
        # 正例：出口话术里的 150% 在简历里没有 → 必须整条剔除
        cv = {'cv.md': '处理 3万 条数据，准确率 92%。'}
        r = pv.gate_outgoing_text('我把准确率提升了 150%，处理量 3万 条', cv)
        self.assertIn('150%', r['blocked'])
        self.assertIn('30000', r['ok'])
        self.assertIn('[数字待核实]', r['stripped_text'])
        self.assertNotIn('150%', r['stripped_text'])

    def test_gate_passes_backed_number(self):
        # 反例：有佐证的数字不该被动
        cv = {'cv.md': '处理 3万 条数据。'}
        r = pv.gate_outgoing_text('我处理过 3万 条数据', cv)
        self.assertEqual(r['blocked'], [])
        self.assertNotIn('[数字待核实]', r['stripped_text'])

    def test_gate_requires_matching_unit(self):
        # ★ 2026-09-12 修的漏网：只比数值时，'5 分钟' 会因为简历里的 '5 个月' 而放行
        cv = {'cv.md': '独立负责 5 个月的项目全过程管理。'}
        r = pv.gate_outgoing_text('我把响应时效压到 5 分钟', cv)
        self.assertTrue(any('5分钟' in b for b in r['blocked']), r['blocked'])
        self.assertIn('[数字待核实]', r['stripped_text'])
        # 单位一致时应当放行
        r2 = pv.gate_outgoing_text('独立负责 5 个月的项目', cv)
        self.assertEqual(r2['blocked'], [])

    def test_gate_leaves_rhetorical_small_numbers_alone(self):
        # 边界：'从 0 到 1' 是修辞不是主张，改掉会把句子弄坏
        cv = {'cv.md': '处理 3万 条数据。'}
        r = pv.gate_outgoing_text('我独立从 0 到 1 交付了两套系统', cv)
        self.assertEqual(r['blocked'], [])
        self.assertIn('从 0 到 1', r['stripped_text'])

    def test_gate_on_empty(self):
        self.assertEqual(pv.gate_outgoing_text('', {'cv.md': 'x'})['blocked'], [])


class TestLiveness(unittest.TestCase):
    def test_curly_quote_normalized(self):
        # ★ career-ops 的真实踩坑：法文用 U+2019，模式写 ASCII → 静默永不匹配
        text = 'Cette offre n\u2019est plus disponible.'
        st, _ = lv.classify(text)
        self.assertEqual(st, lv.EXPIRED)

    def test_chinese_expired(self):
        st, _ = lv.classify('该职位已过期，感谢关注')
        self.assertEqual(st, lv.EXPIRED)

    def test_bot_challenge_is_never_expired(self):
        # ★ 核心约束：反爬插页必须判"不确定"，否则活岗会被永久过滤掉
        for t in ['Just a moment...', 'Enable JavaScript and cookies to continue',
                  '请完成安全验证', '访问过于频繁']:
            st, _ = lv.classify(t)
            self.assertEqual(st, lv.CHALLENGE, t)
            self.assertFalse(lv.should_filter_out(st), '挑战页绝不能写历史/拉黑')

    def test_filled_guard_excludes_form_and_filled_out(self):
        # 正例：真·招满
        self.assertEqual(lv.classify('the job you are trying to apply for has been filled')[0], lv.EXPIRED)
        # 反例：填表句不能判过期（否则把活岗读成过期）
        live = ('Once the application form has been filled you will receive a confirmation email. ' +
                'We are hiring a Python engineer to build RAG pipelines with our team in Shenzhen. ' +
                'Responsibilities include knowledge base design, vector search tuning, and Agent ' +
                'workflow delivery. We offer competitive salary, flexible hours, and a friendly team. ' +
                'Requirements: 3+ years with Python, hands-on LLM API experience, and strong ' +
                'communication skills. apply now to join us in building the next generation of tooling.')
        self.assertGreater(len(live), lv.MIN_CONTENT_CHARS, '测试样本必须够长，否则会走"内容不足"分支')
        self.assertEqual(lv.classify(live)[0], lv.ACTIVE)

    def test_insufficient_is_uncertain(self):
        st, _ = lv.classify('短')
        self.assertEqual(st, lv.INSUFFICIENT)
        self.assertFalse(lv.should_filter_out(st))

    def test_listing_page(self):
        st, _ = lv.classify('共 128 个职位' + 'x' * 300)
        self.assertEqual(st, lv.LISTING)

    def test_only_expired_is_filterable(self):
        self.assertTrue(lv.should_filter_out(lv.EXPIRED))
        for s in lv.UNCERTAIN_STATES:
            self.assertFalse(lv.should_filter_out(s))


class TestBlockG(unittest.TestCase):
    def test_score_impact_always_none(self):
        # ★ 核心约束：Block G 绝不参与评分（否则历史趋势不可比）
        a = bg.assess(posting_age_days=5, apply_button_active=True,
                      jd_text='Python RAG Agent 知识库 大模型 API Docker')
        self.assertEqual(a.score_impact, 'NONE')

    def test_suspicious_on_high_reliability_negative(self):
        a = bg.assess(posting_age_days=200, apply_button_active=False, jd_text='', repost_count_90d=4)
        self.assertEqual(a.tier, bg.SUSPICIOUS)

    def test_high_confidence_when_clean(self):
        a = bg.assess(posting_age_days=7, apply_button_active=True,
                      jd_text='负责 RAG 知识库与 Agent 工作流，Python + SQL + Docker，经验 3 年以上')
        self.assertEqual(a.tier, bg.HIGH_CONFIDENCE)

    def test_thin_evidence_is_not_high_confidence(self):
        # ★ 2026-09-12 修的假自信：高可靠性信号（挂了多久/Apply按钮）都没采集时，
        #   不能仅凭 medium 正分就判"高置信"
        a = bg.assess(posting_age_days=None, apply_button_active=None, jd_text='AI 实施方向')
        self.assertNotEqual(a.tier, bg.HIGH_CONFIDENCE)
        self.assertEqual(a.tier, bg.CAUTION)

    def test_legitimate_explanations_always_offered(self):
        # ★ 强制伦理框架：有可疑信号时必须同时给合理解释，且不下"不诚信"的指控
        a = bg.assess(posting_age_days=120, apply_button_active=False)
        self.assertTrue(a.legitimate_explanations)
        rep = a.report()
        self.assertIn('不代表对方不诚信', rep)

    def test_requirement_contradiction_detected(self):
        a = bg.assess(jd_text='应届毕业生，要求 5 年及以上工作经验')
        names = {s.name: s for s in a.signals}
        self.assertEqual(names['要求是否自相矛盾'].verdict, 'concerning')


class TestRepostDetect(unittest.TestCase):
    def _rows(self):
        return [
            {'company': 'acme', 'title': 'Solutions Engineer', 'url': 'u1',
             'first_seen': '2026-06-01', 'status': 'added'},
            {'company': 'acme', 'title': 'Solutions Engineer', 'url': 'u2',
             'first_seen': '2026-06-25', 'status': 'added'},
        ]

    def test_repost_found_with_span(self):
        got = rd.find_reposts(self._rows())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['count'], 2)

    def test_concurrent_sweep_is_not_a_repost(self):
        # ★ 最小跨度约束：同一次扫描发现的（同一天）是并发岗，不是重发
        rows = [
            {'company': 'acme', 'title': 'Solutions Engineer', 'url': 'u1',
             'first_seen': '2026-06-01', 'status': 'added'},
            {'company': 'acme', 'title': 'Solutions Engineer', 'url': 'u2',
             'first_seen': '2026-06-01', 'status': 'added'},
        ]
        self.assertEqual(rd.find_reposts(rows), [])

    def test_title_identity_not_similarity(self):
        # ★ 核心约束：只在城市上不同的标题是不同 requisition，绝不合并
        m = rd.title_identity_key('Commercial Solutions Engineer - Munich')
        b = rd.title_identity_key('Commercial Solutions Engineer - Berlin')
        self.assertNotEqual(m, b)
        # 但词序/标点变化应视为同一条
        self.assertEqual(rd.title_identity_key('Solutions Engineer, Commercial'),
                         rd.title_identity_key('Commercial  Solutions Engineer'))

    def test_aggregator_skipped(self):
        rows = self._rows()
        self.assertEqual(rd.find_reposts(rows, aggregators=['acme']), [])

    def test_dead_statuses_ignored(self):
        rows = self._rows()
        for r in rows:
            r['status'] = 'skipped_expired'
        self.assertEqual(rd.find_reposts(rows), [])

    def test_outside_window_ignored(self):
        rows = self._rows()
        rows[0]['first_seen'] = '2025-01-01'   # 跨度 > 90 天
        self.assertEqual(rd.find_reposts(rows), [])


class TestFunnelStats(unittest.TestCase):
    def test_right_censoring_reported(self):
        # ★ 核心规则：右删失样本必须报出来，不能只算已完结的
        r = fs.median_with_censoring([3, 5, 7], censored_count=12, label='回复天数')
        self.assertEqual(r['median'], 5)
        self.assertEqual(r['censored'], 12)
        self.assertIn('右删失', r['note'])
        self.assertIn('幸存者偏差', r['note'])

    def test_no_sample_no_median(self):
        r = fs.median_with_censoring([], censored_count=5)
        self.assertIsNone(r['median'])

    def test_small_n_blocks_comparison(self):
        # ★ 核心规则：n<20 不许给倍数结论
        r = fs.calibrate(0.30, 8, {'low': 0.05, 'high': 0.10, 'year': 2026})
        self.assertFalse(r['comparable'])
        self.assertTrue(any('不给任何倍数' in n for n in r['notes']))

    def test_benchmark_carries_year_and_directional(self):
        r = fs.calibrate(0.07, 25, {'low': 0.05, 'high': 0.10, 'year': 2026})
        joined = ' '.join(r['notes'])
        self.assertIn('2026', joined)
        self.assertIn('方向性', joined)
        self.assertTrue(r['comparable'])

    def test_above_range_gets_selection_bias_note(self):
        # ★ 核心规则：超出区间必须带选择偏差提示
        r = fs.calibrate(0.40, 25, {'low': 0.05, 'high': 0.10, 'year': 2026})
        self.assertEqual(r['position'], 'above_range')
        self.assertTrue(any('选择偏差' in n for n in r['notes']))

    def test_zero_day_hops_excluded_but_counted(self):
        v = fs.stage_velocity([
            {'from': 'Applied', 'to': 'Responded', 'days': 0},
            {'from': 'Applied', 'to': 'Responded', 'days': 4},
            {'from': 'Applied', 'to': 'Responded', 'days': 6},
        ])
        hop = v['Applied → Responded']
        self.assertEqual(hop['n'], 2)                  # 0 天被排除出中位数
        self.assertEqual(hop['excluded_zero_day'], 1)  # 但仍计数
        self.assertEqual(hop['median_days'], 5)


class TestInterviewPrep(unittest.TestCase):
    class _Story:
        def __init__(self, title, raw, labels=None):
            self.title = title
            self.raw = raw
            self.labels = labels or {}

    def test_audience_buckets_present(self):
        q = ip.derive_questions('负责 RAG 知识库与大模型应用开发，Python + SQL')
        self.assertIn(ip.RECRUITER, q)
        self.assertIn(ip.PEER, q)
        self.assertTrue(any('RAG' in x or '检索' in x for x in q[ip.PEER]))

    def test_gap_when_no_story(self):
        # ★ 核心设计：没有对应故事必须显式列为缺口，不能静默略过
        rep = ip.build_prep('某公司', 'AI应用工程师', '负责 RAG 知识库开发', stories=[])
        self.assertTrue(rep.gaps)
        self.assertTrue(all('STAR+R' in g for g in rep.gaps))
        self.assertIn('❌', rep.render())

    def test_non_behavioral_questions_are_not_gaps(self):
        # ★ 2026-09-12 修：自我介绍/薪资/到岗不是行为题，不该被要求配故事
        self.assertFalse(ip.needs_story('请用一分钟介绍你自己，以及为什么投这个岗位。'))
        self.assertFalse(ip.needs_story('期望薪资是多少？'))
        self.assertFalse(ip.needs_story('最快什么时候能到岗？'))
        self.assertTrue(ip.needs_story('讲一个你独立从 0 到 1 交付的东西，最难的地方在哪？'))
        rep = ip.build_prep('某公司', 'AI岗', 'RAG', stories=[])
        self.assertFalse(any('自我介绍' in g for g in rep.gaps),
                         '自我介绍不该出现在故事缺口里')
        self.assertIn('非行为题', rep.render())

    def test_story_matched_and_marked(self):
        s = self._Story('知识库交付', '负责 RAG 知识库 检索 向量 交付 上线 客户')
        rep = ip.build_prep('某公司', 'AI应用工程师', '负责 RAG 知识库开发', stories=[s])
        hits = [m for m in rep.mapping if m.fit != 'none']
        self.assertTrue(hits)

    def test_stated_comp_consistency_reminder(self):
        rep = ip.build_prep('某公司', 'AI岗', 'RAG', stories=[], stated_comp='18K')
        self.assertTrue(any('18K' in r for r in rep.reminders))
        self.assertTrue(any('保持一致' in r for r in rep.reminders))

    def test_provenance_gate_reminder_always(self):
        rep = ip.build_prep('某公司', 'AI岗', 'RAG', stories=[])
        self.assertTrue(any('逐字佐证' in r for r in rep.reminders))

    def test_energy_management_on_long_process(self):
        rep = ip.build_prep('某公司', 'AI岗', 'RAG', stories=[], stage_count=4)
        self.assertTrue(any('体力' in r for r in rep.reminders))


class TestTechClaims(unittest.TestCase):
    """技术栈/项目声明核验（2026-09-15 新增，career-ops 只核数字没有这层）。

    真实触发案例：自动起草的 HR 回复写了「Chroma+BGE 的 RAG 匹配引擎」，
    本地磁盘 + GitHub 12 个仓库双向核查都没有实物 → 必须拦。
    """

    def test_unsupported_when_absent(self):
        corpus = {'cv.md': 'Python / Dify / 浏览器自动化，做过装修获客 AI 客服'}
        r = pv.check_tech_claims('我搭过 Chroma+BGE 的 RAG 匹配引擎', corpus)
        self.assertIn('Chroma 向量库', r['unsupported'])
        self.assertIn('BGE 向量模型', r['unsupported'])
        self.assertTrue(r['checked'] >= 2)

    def test_supported_when_present(self):
        # Chrome 扩展：源语料里出现 manifest.json / chrome-extension 才算有实物
        corpus = {'github-repos': 'boss-zhipin-helper | AI 驱动的求职 Chrome 扩展 | '
                                  'javascript | ai chrome-extension manifest-v3'}
        r = pv.check_tech_claims('我做过 BOSS直聘助手 Chrome 扩展', corpus)
        self.assertEqual(r['unsupported'], [])
        self.assertTrue(any(label == 'Chrome 扩展' for label, _ in r['supported']))

    def test_only_checks_claims_present_in_text(self):
        # 草稿没提到的技术不该进统计（否则报告全是噪音）
        corpus = {'cv.md': 'Python'}
        r = pv.check_tech_claims('你好，方便聊一下岗位吗？', corpus)
        self.assertEqual(r['checked'], 0)
        self.assertEqual(r['unsupported'], [])

    def test_real_case_corpus_boundary(self):
        # 面试准备文档不算源事实 —— 只有真实项目自述算
        draft = '我做过 95分球鞋监控（API逆向 + 视觉 LLM 精筛）'
        with_repo = {'95fen-monitor': '95fen-monitor 逆向 95分 App 签名，qwen 看图识鞋'}
        without = {'interview-prep': '可以讲 RAG 项目的 Chroma 向量库'}
        self.assertEqual(pv.check_tech_claims(draft, with_repo)['unsupported'], [])
        self.assertIn('95分/球鞋监控', pv.check_tech_claims(draft, without)['unsupported'])


class TestUnitBoundaryRegex(unittest.TestCase):
    """★ 2026-09-12 踩坑：词边界必须 ASCII-only。

    Python 3 的 \\w 把中文也算单词字符 → 数字匹配到单位后，后面紧跟的「的」会让
    lookahead 失败 → 回退成只匹配数字，**单位被静默丢弃**，于是 '5 分钟' 被当成 '5'。
    """

    def test_chinese_after_unit_does_not_drop_unit(self):
        claims = pv.extract_claims('独立负责 5 个月的项目全过程管理。')
        self.assertIn(('5', '个月'), claims)

    def test_unit_at_end_of_string(self):
        self.assertIn(('5', '分钟'), pv.extract_claims('响应时效压到 5 分钟'))

    def test_ascii_identifier_not_split(self):
        # 反例：标识符里的数字不该被当成主张
        self.assertEqual(pv.extract_claims('变量 abc123def 的值'), [])


if __name__ == '__main__':
    unittest.main()

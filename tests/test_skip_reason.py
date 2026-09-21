#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跳过原因埋点回归（2026-09-20）。

【为什么有这个文件】
Boss 端从 2026-08-23 起有 **1048 条 SKIPPED 的 reason 是空串**，全部
`event=deep_filter`、`score=0`、岗位一家都没被 deep_filter 拦过。追下去是一条链：

  1. `shared.score_jd` 按标题排除词正确地把分数归零，并**给出了原因**
     （「标题包含排除词: 应届」），reason 变量是好的；
  2. `deep_filter` 跑完 7 条检测规则一条都没命中，走到 Priority 8
     `return score, ""` —— 它**原样返回入参 score**，于是返回 (0, "")；
  3. `boss_apply` 的判据写的是 `if deep_score == 0` —— 把「入参本来就是 0 分」
     当成了「deep_filter 拦截」，于是把第 1 步的正确原因**用一个空串覆盖**后落库，
     event 还记成了 `deep_filter`。

所以这 1048 条不是「规则在拦人」，是**埋点把原因丢了**。岗位该不该跳过没变
（score 0 本来就过不了 `min_score`，配置里是 10），变的是我们事后能不能查。

【怎么测的】
不测 is_filtered 这个两行函数的真值表而已 —— 那样把调用点改回 `== 0` 测试照样绿。
这里直接跑**真实的 `_prepare_job_context`**：真 `score_jd`、真 `deep_filter`、
真 `job_decision.evaluate_job`，只把「要连浏览器/要发网络请求」的那几环替换掉
（详情面板、公司背调、语义层、五维评估）。断言的是**落库那一刻的 reason 与 event**。

每条用例都带对照组：先断言旧判据 `deep_score == 0` 在这条链上确实为真 ——
证明这个桩真的能复现那 1048 条，否则用例就是永远绿的摆设。

§8 铁律：全程不连浏览器、不发任何网络请求，run_js/ele 都是桩。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import boss_apply as BA          # noqa: E402
import deep_filter as DF         # noqa: E402
import shared as SH              # noqa: E402

# 用户是 2025 届、两年择业期内 —— 但「应届」这两个字在 config.json 的
# exclude_keywords 里，Boss 路径只拿得到标题，于是一整批正常岗位被标题排除词归零。
# 这里照抄那个真实场景，不依赖 config.json 的当前内容（用户随时会改）。
_TITLE = "软开&软测&AI(接受应届导师1带1）"
_COMPANY = "某某数字科技有限公司"   # 中性公司：不得命中公司红线/黑名单，否则走不到「标题排除词」这条路径
_SALARY = "10-15K·12薪"
_DESC = "负责企业级 AI 应用开发，双休，五险一金"


def _cfg():
    """最小配置：只保留本用例要走的两个字段。"""
    return {
        "exclude_keywords": ["应届"],
        "must_contain": [],
        "target_roles": ["AI"],
        "skills": ["AI"],
        "boost_keywords": [],
        "min_score": 10,
    }


class _El:
    """详情面板桩：`.text` 就是 JD 正文。"""

    def __init__(self, text):
        self.text = text


class _Tab:
    """search_tab 桩：只实现 _prepare_job_context 在这条链上用到的两个方法。"""

    def __init__(self, desc=""):
        self._desc = desc
        self.js_calls = []

    def run_js(self, js, *a, **k):
        self.js_calls.append(js)
        return None       # 点卡片/查按钮：这条链上不需要返回值

    def ele(self, sel):
        return _El(self._desc) if self._desc else None


def _run_prepare(*, title, company, salary, desc, cfg=None, min_score=10):
    """跑真实的 _prepare_job_context，返回 (动作, 落库时 captured 的入参 dict)。"""
    recorded = []

    def _capture(city, comp, ttl, sal, kw, score, reason, **kw2):
        recorded.append({"city": city, "company": comp, "title": ttl, "salary": sal,
                         "keyword": kw, "score": score, "reason": reason, **kw2})

    tab = _Tab(desc)
    ctx = {}
    # 语义层 / Plan 路由 / 五维 / 背调都要连网或另有单测，这里降级为「不拦截」，
    # 让链条走到我们真正要验的 deep_filter → min_score 那一段。
    with mock.patch.object(BA, "_record_outcome", _capture), \
         mock.patch.object(BA.time, "sleep"), \
         mock.patch.object(BA, "run_company_background_check",
                           return_value={"kind": "ok", "total": 0, "jobs": []}), \
         mock.patch.object(BA, "explain_match",
                           return_value={"total": 60, "verdict": "PASS",
                                         "risks": [], "dimensions": {}}), \
         mock.patch("semantic_parser.gate", return_value=""), \
         mock.patch("plan_router.route_plan",
                    return_value=mock.Mock(plan="P1-A", slot="P1-A", reason="")):
        action = BA._prepare_job_context(tab, "深圳", "AI实施", title, company,
                                         salary, cfg or _cfg(), min_score, ctx)
    return action, recorded, ctx


class TestDeepFilterContract(unittest.TestCase):
    """deep_filter 的返回值语义：判据是 reason，不是 score。"""

    JH_CATEGORY = "skip_reason"
    JH_NEW = True

    def test_pass_through_keeps_the_input_score(self):
        """未命中任何规则时**原样返回入参 score** —— 传 0 就返回 (0, "")。"""
        score, reason = DF.deep_filter("某某科技", "AI应用工程师", "双休", "12-18K", 0)
        self.assertEqual(score, 0)
        self.assertEqual(reason, "")
        r50, _ = DF.deep_filter("某某科技", "AI应用工程师", "双休", "12-18K", 50)
        self.assertEqual(r50, 50, "未命中规则时不得改动入参 score")

    def test_is_filtered_judges_on_reason_only(self):
        self.assertFalse(DF.is_filtered(0, ""), "(0, '') 是「通过」，不是「拦截」")
        self.assertTrue(DF.is_filtered(0, "标题党:标题含AI但正文无AI技术词"))
        self.assertTrue(DF.is_filtered(60, "公司名含「人力资源」→人力中介/代招风险"))

    def test_real_detection_still_reads_as_filtered(self):
        """防矫枉过正：真被拦下的岗位必须仍判为拦截。"""
        score, reason = DF.deep_filter("某某人力资源有限公司", "AI应用工程师",
                                       "双休", "12-18K", 60)
        self.assertTrue(DF.is_filtered(score, reason))
        self.assertIn("人力资源", reason)


class TestEmptyReasonRegression(unittest.TestCase):
    """那 1048 条的端到端回归：reason 必须落库，不许被空串覆盖。"""

    JH_CATEGORY = "skip_reason"
    JH_NEW = True

    def test_score_zero_survives_deep_filter_with_its_reason(self):
        action, recorded, ctx = _run_prepare(title=_TITLE, company=_COMPANY,
                                             salary=_SALARY, desc=_DESC)

        # ── 对照组：证明这个桩真的复现了那 1048 条 ──
        # 旧判据 `if deep_score == 0` 在这条链上必然为真 → 旧代码会走拦截分支。
        ds, dr = DF.deep_filter(_COMPANY, _TITLE, _DESC, _SALARY, 0)
        self.assertEqual((ds, dr), (0, ""),
                         "桩没造出「入参 0 分 + deep_filter 未命中」的场景，用例失去意义")
        self.assertTrue(ds == 0, "旧判据 deep_score == 0 在这条链上必须为真")

        # ── 正题 ──
        self.assertEqual(action, "skip", "score=0 仍然要跳过（min_score 门槛拦下）")
        self.assertEqual(len(recorded), 1, "应当恰好落库一次，不得重复记账")
        row = recorded[0]
        self.assertTrue(row["reason"].strip(),
                        f"reason 落成了空串 —— 又变成那 1048 条了：{row}")
        self.assertIn("应届", row["reason"], "真实原因（标题排除词）必须落库")
        self.assertNotEqual(row.get("event"), "deep_filter",
                            "岗位根本没被 deep_filter 拦，不该记成 deep_filter")
        self.assertEqual(row.get("event"), "below_min_score")

    def test_real_deep_filter_block_still_recorded_as_deep_filter(self):
        """反向：真被 deep_filter 拦下的岗位，event 与 reason 照旧。

        用标题党（标题含 AI、正文是传统开发）当触发器 —— deep_filter 在这一条上是
        「真的把 35 分改写成 0 分」，与上面那条「入参本来就是 0 分」正好互为对照。
        """
        action, recorded, _ = _run_prepare(title="AI应用工程师", company="某某科技有限公司",
                                           salary=_SALARY,
                                           desc="负责Java后端开发，Spring框架，双休")
        self.assertEqual(action, "skip")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0].get("event"), "deep_filter",
                         "真拦截被放过去了 —— 修过头了")
        self.assertIn("标题党", recorded[0]["reason"])

    def test_normal_job_is_not_blocked_by_either_guard(self):
        """防矫枉过正：正常高分岗位必须走到 proceed，不许被两处判据顺手拦掉。"""
        action, recorded, ctx = _run_prepare(title="AI应用工程师", company="某某科技有限公司",
                                             salary=_SALARY, desc=_DESC, min_score=10)
        self.assertEqual(recorded, [], f"不应有任何跳过记账：{recorded}")
        self.assertNotEqual(action, "skip")


class TestGuardStructure(unittest.TestCase):
    """结构：两个调用点都必须走 is_filtered()，不许再出现 `== 0` 的裸判据。"""

    JH_CATEGORY = "skip_reason"
    JH_NEW = True

    @staticmethod
    def _code_lines(path):
        """只留会真执行的代码行 —— 注释里的举例（「原来写的是 if deep_score == 0」）
        不该被判据自己误伤。"""
        return [ln for ln in Path(path).read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]

    def test_no_bare_zero_check_on_deep_filter_results(self):
        lines = self._code_lines(BA.__file__)
        for bad in ("if deep_score == 0", "if prof_score == 0"):
            self.assertFalse(any(bad in ln for ln in lines),
                             f"`{bad}` 会把「入参 0 分」误判成 deep_filter 拦截")
        n = sum(ln.count("if is_filtered(") for ln in lines)
        self.assertEqual(n, 2, "deep_filter 的两个调用点都应改用 is_filtered()")

    def test_is_filtered_is_imported_from_deep_filter(self):
        """判据只允许有一份实现（收敛到 deep_filter.is_filtered）。"""
        self.assertIs(BA.is_filtered, DF.is_filtered)


if __name__ == "__main__":
    unittest.main()

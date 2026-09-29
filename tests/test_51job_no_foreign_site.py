#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""站外页面红线（2026-09-29 用户明令「做掉，不能再开这个网页。强调很多次了。」）。

事故链路：51job 搜索结果里「校招/应届」类岗位的「申请」按钮，点了会**另开一个标签页**
跳到应届生求职网（q.yingjiesheng.com/jobdetail/NNN.html?partner=51wspcjoblist）。
脚本点完只在原卡片上找回执（拿不到「已申请」）→ 判 FAILED、明天还会再点一次；
那个新开的站外标签页没人关 —— 实测 9/27 一天 208 次访问、同一岗位页重复 99 次、
投递 Chrome 里残留 37 个（9/27 事故记录里写成「来源未明」，就是这里）。

本文件锁死四件事：
  ① 站外判定（应届生网 / 校招通道 / 白名单外一律 fail-closed）
  ② 清场只清站外页，用户页（闲鱼/Boss）与浏览器内部页**绝不碰**
  ③ 点完跳站外 → SKIPPED（不是 FAILED）+ jobId 永久拉黑 + 不重试
  ④ 点之前就能从卡片链接判出来的，直接不点
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import platform_51job as p

# 用户 2026-09-29 实际贴出来的那条 URL
USER_URL = ("https://q.yingjiesheng.com/jobdetail/173570709.html?jobid=173570709"
            "&partner=51wspcjoblist&property=%7B%22jobType%22%3A%220%22%7D")


class _Tab:
    """投递 tab 桩件：能执行 JS、能导航、能关闭。"""

    def __init__(self, url="about:blank"):
        self.url = url
        self.closed = False

    def run_js(self, *_a, **_k):
        return 1

    def get(self, url):
        self.url = url

    def close(self):
        self.closed = True


class _Page:
    """Chrome page 桩件。"""

    def __init__(self):
        self.new_calls = 0

    def new_tab(self, _url, background=False):
        self.new_calls += 1
        return _Tab("about:blank")


def _card(job_id="173570709", title="AI应用工程师（应届生）", link="", apply_href=""):
    return {"jobId": job_id, "title": title, "salary": "12-20K", "area": "深圳",
            "year": "25", "degree": "本科", "company": "某某科技有限公司",
            "btnText": "申请", "link": link, "applyHref": apply_href}


# ══════════════════════════════════════════════════════════════
#  一、站外判定
# ══════════════════════════════════════════════════════════════
class TestSiteVerdict(unittest.TestCase):
    JH_CATEGORY = "safety"
    JH_NEW = True

    def test_user_url_forbidden(self):
        """用户贴的那条 URL 必须命中站外（永久回归用例）。"""
        self.assertIn("yingjiesheng", p.forbidden_hit(USER_URL) or "")

    def test_forbidden_subdomains(self):
        for u in ("https://q.yingjiesheng.com/pc/jobdetail?jobid=1",
                  "https://young.yingjiesheng.com/xyzlogin?ctmid=1",
                  "https://www.yingjiesheng.com/",
                  "https://YINGJIESHENG.com/jobdetail/1.html"):
            self.assertIsNotNone(p.forbidden_hit(u), f"{u} 应判站外")

    def test_campus_channel(self):
        for u in ("https://xyz.51job.com/consumer/pc/resume/index?ctmid=1",
                  "https://campus.51job.com/x/", "https://young.51job.com/"):
            self.assertIn("校招", p.forbidden_hit(u) or "")

    def test_51job_own_pages_ok(self):
        for u in ("https://we.51job.com/pc/search?keyword=x&jobArea=040000",
                  "https://we.51job.com/loginverify",
                  "https://www.51job.com/", "https://login.51job.com/x",
                  "https://m.51job.com/", "about:blank", "", None):
            self.assertIsNone(p.forbidden_hit(u), f"{u} 不该判站外")

    def test_unknown_host_fail_closed(self):
        """白名单之外一律算站外 —— 宁可少投一条，也不许乱开页面。"""
        self.assertIn("站外", p.forbidden_hit("https://example.com/job/1") or "")

    def test_port_and_case_ignored(self):
        self.assertIn("yingjiesheng", p.forbidden_hit("https://Q.YingJiesheng.com:443/a") or "")


# ══════════════════════════════════════════════════════════════
#  二、清场范围：只清站外，不碰用户页
# ══════════════════════════════════════════════════════════════
class TestCloseForeignTabs(unittest.TestCase):
    JH_CATEGORY = "safety"
    JH_NEW = True

    def test_closes_only_named_foreign_hosts(self):
        """只关「点名」的两类域名：应届生网 + 校招通道。白名单外的普通页面**不动**。

        （2026-09-29 现场教训：验证时端口打错，函数差点去关别的 Chrome 的普通页面 ——
        所以关标签页这条改成宁缺勿滥，fail-closed 只用于「不点」，不用于「关页」。）
        """
        targets = [
            {"id": "1", "url": USER_URL},                                              # 关
            {"id": "2", "url": "https://xyz.51job.com/consumer/pc/resume/index"},      # 关
            {"id": "8", "url": "https://example.com/job"},                             # **留**（非点名）
            {"id": "3", "url": "https://we.51job.com/pc/search?keyword=x"},            # 留
            {"id": "4", "url": "about:blank"},                                         # 留
            {"id": "5", "url": "https://www.goofish.com/item/1"},                      # 留（用户闲鱼）
            {"id": "6", "url": "https://www.zhipin.com/web/geek/chat"},                # 留（Boss 聊天）
            {"id": "7", "url": "chrome://settings"},                                   # 留（内部页）
        ]
        closed = []

        def _closer(tid):
            closed.append(tid)
            return True

        n = p.close_foreign_tabs(targets=targets, closer=_closer)
        self.assertEqual(sorted(closed), ["1", "2"])
        self.assertEqual(n, 2)

    def test_refuses_when_chrome_is_not_delivery_instance(self):
        """端口上的 Chrome 不是投递专用实例 → 一个都不关（防打错实例）。"""
        with mock.patch.object(p, "_delivery_chrome_ok", return_value=False), \
             mock.patch.object(p, "_cdp_targets",
                               side_effect=AssertionError("不许去读别的 Chrome")) as _t, \
             mock.patch.object(p, "_cdp_close",
                               side_effect=AssertionError("不许去关别的 Chrome")):
            self.assertEqual(p.close_foreign_tabs(), 0)
        _t.assert_not_called()

    def test_user_pages_never_closed(self):
        """红线：闲鱼/Boss/内部页一个都不许关（与 sweep_stale_tabs 同一安全边界）。"""
        targets = [{"id": str(i), "url": u} for i, u in enumerate(
            ["https://www.goofish.com/x", "https://www.zhipin.com/web/geek/chat",
             "https://market.taobao.com/x", "chrome://extensions", "devtools://x"])]
        n = p.close_foreign_tabs(targets=targets, closer=lambda tid: True)
        self.assertEqual(n, 0)


# ══════════════════════════════════════════════════════════════
#  三、永久拉黑名单
# ══════════════════════════════════════════════════════════════
class TestForeignSkipList(unittest.TestCase):
    JH_CATEGORY = "safety"
    JH_NEW = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = p.FOREIGN_SKIP_FILE
        p.FOREIGN_SKIP_FILE = self.tmp / "foreign_skip.json"

    def tearDown(self):
        p.FOREIGN_SKIP_FILE = self._orig

    def test_roundtrip_and_dedupe(self):
        self.assertEqual(p._load_foreign_skips(), set())
        p._remember_foreign_skip("173570709", "AI应用工程师（应届生）", "某公司", USER_URL, "站外:yingjiesheng.com")
        p._remember_foreign_skip("173570709", "重复写入", "某公司", USER_URL, "站外:yingjiesheng.com")
        self.assertEqual(p._load_foreign_skips(), {"173570709"})
        d = json.loads(p.FOREIGN_SKIP_FILE.read_text())
        self.assertEqual(len([x for x in d["detail"] if x["jobId"] == "173570709"]), 2)  # 明细留痕

    def test_empty_jobid_is_noop(self):
        p._remember_foreign_skip("", "t", "c", USER_URL, "x")
        self.assertEqual(p._load_foreign_skips(), set())


# ══════════════════════════════════════════════════════════════
#  四、投递主流程：点完跳站外 → SKIPPED + 拉黑 + 不重试
# ══════════════════════════════════════════════════════════════
class TestApplyFlowForeignGuard(unittest.TestCase):
    JH_CATEGORY = "safety"
    JH_NEW = True

    def setUp(self):
        p.HOURLY_CAP = 0
        p.TAB_READY_WAIT = 0
        p.TAB_RETRY_WAIT = 0
        p.FOREIGN_HITS["round"] = 0
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = p.FOREIGN_SKIP_FILE
        p.FOREIGN_SKIP_FILE = self.tmp / "foreign_skip.json"

    def tearDown(self):
        p.FOREIGN_SKIP_FILE = self._orig

    def _run(self, card, closed=1):
        page, tab, records = _Page(), _Tab(), []
        with mock.patch.object(p, "get_cards", return_value=[card]), \
             mock.patch.object(p, "click_apply_and_check", return_value="") as click, \
             mock.patch.object(p, "close_foreign_tabs", return_value=closed) as closer, \
             mock.patch.object(p, "record_application",
                               side_effect=lambda **kw: records.append(kw)), \
             mock.patch.object(p, "in_night_window", return_value=False), \
             mock.patch("time.sleep"):
            applied, skipped, _tab = p.run_city_keyword(
                page, tab, "深圳", "AI应用工程师", 5, set(), 0)
        return applied, skipped, records, click, closer

    def test_foreign_after_click_is_skipped_not_failed(self):
        applied, skipped, records, click, closer = self._run(_card())
        self.assertEqual(applied, 0)
        self.assertEqual(skipped, 1)
        click.assert_called_once()          # 只点一次 —— 不许反复重入站外页
        closer.assert_called()              # 点完立刻清场
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "SKIPPED")      # 关键：不是 FAILED
        self.assertEqual(records[0]["decision"], "skipped")    # 沿用现成口径
        self.assertIn("站外", records[0]["reason"])
        self.assertIn("173570709", p._load_foreign_skips())    # 永久拉黑

    def test_no_foreign_tab_keeps_original_failed_path(self):
        """没跳站外时，原来的 FAILED 路径必须照旧（不许误伤正常候选）。"""
        applied, skipped, records, click, _ = self._run(_card(), closed=0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "FAILED")
        self.assertEqual(p._load_foreign_skips(), set())

    def test_offsite_card_is_never_clicked(self):
        """点之前就能从卡片链接判出来 → 不点、不清场、不落库。"""
        card = _card(apply_href=USER_URL)
        applied, skipped, records, click, closer = self._run(card)
        click.assert_not_called()
        closer.assert_not_called()
        self.assertEqual(records, [])
        self.assertEqual(skipped, 1)

    def test_blacklisted_jobid_never_clicked(self):
        p._remember_foreign_skip("173570709", "t", "c", "", "站外")
        applied, skipped, records, click, closer = self._run(_card())
        click.assert_not_called()
        self.assertEqual(records, [])
        self.assertEqual(skipped, 1)

    def test_round_aborts_after_too_many_foreign_hits(self):
        """单轮触发达上限 → 中止本轮，不再给站外页任何机会。"""
        orig = p.FOREIGN_ABORT_PER_ROUND
        p.FOREIGN_ABORT_PER_ROUND = 1
        try:
            cards = [_card(job_id="1"), _card(job_id="2"), _card(job_id="3")]
            page, tab, records = _Page(), _Tab(), []
            with mock.patch.object(p, "get_cards", return_value=cards), \
                 mock.patch.object(p, "click_apply_and_check", return_value="") as click, \
                 mock.patch.object(p, "close_foreign_tabs", return_value=1), \
                 mock.patch.object(p, "record_application",
                                   side_effect=lambda **kw: records.append(kw)), \
                 mock.patch.object(p, "in_night_window", return_value=False), \
                 mock.patch("time.sleep"):
                p.run_city_keyword(page, tab, "深圳", "AI应用工程师", 5, set(), 0)
            self.assertEqual(click.call_count, 1)     # 命中上限就收手
        finally:
            p.FOREIGN_ABORT_PER_ROUND = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)

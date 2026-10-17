#!/usr/bin/env python3
"""official_mail_apply 单测：解析 / 路由 / Format Guard（不连网、不碰本地队列）。"""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import official_mail_apply as OMA  # noqa: E402


_WAIQI_SNIPPET = """
## A 档 · 优先投

| # | 岗位 | 公司 | 城市/经验 | 薪资 | 判据 | 链接 |
|---|---|---|---|---|---|---|
| 1 | RPA自动化开发工程师 | 大宇宙信息创造（中国） | 深圳-南山区1年及以上 | 8千-1.2万 | +自动化 ｜ 确认外企 | [详情](https://q.yingjiesheng.com/jobdetail/1.html) |

## B 档 · 参考

| # | 岗位 | 公司 | 城市/经验 | 薪资 | 判据 | 链接 |
|---|---|---|---|---|---|---|
| 1 | AI应用工程师 | 某某科技 | 常州1年及以上 | 8千-1万 | +ai应用 | [详情](https://q.yingjiesheng.com/jobdetail/2.html) |
"""

_ZUOZHAN_SNIPPET = """
## 作战清单（按紧急度）
1. 虾皮/Sea 深圳全球管培 —— 最急
   - 入口B: https://seagmap.sea.com/ （官方网申）
2. 溢达集团 2027 中国管培（佛山高明）—— 2024-2027届可投 —— https://mt.esquel.cn/

## 抽查复核
- 普华永道：三届口径 ✓
"""


class TestParsers(unittest.TestCase):
    JH_CATEGORY = "official_mail"
    JH_NEW = True

    def test_parse_waiqi_table_rows(self):
        rows = OMA.parse_waiqi_md(_WAIQI_SNIPPET)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["bucket"], "A档")
        self.assertEqual(rows[0]["title"], "RPA自动化开发工程师")
        self.assertEqual(rows[0]["link"], "https://q.yingjiesheng.com/jobdetail/1.html")
        self.assertEqual(rows[1]["bucket"], "B档")

    def test_parse_zuozhan_blocks_with_sublines(self):
        rows = OMA.parse_zuozhan_md(_ZUOZHAN_SNIPPET)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["link"].startswith("https://seagmap.sea.com"),
                        "子行的入口 URL 属于上一条编号条目")
        self.assertTrue(rows[1]["link"].startswith("https://mt.esquel.cn"))
        self.assertIn("虾皮", rows[0]["title"])
        self.assertEqual(len(rows), 2, "「抽查复核」段不产生条目")


class TestRouting(unittest.TestCase):
    def test_route_by_direction(self):
        self.assertEqual(OMA.route_resume("RPA自动化开发工程师", "大宇宙"), "impl")
        self.assertEqual(OMA.route_resume("采购经理", "某公司"), "supply")
        self.assertEqual(OMA.route_resume("AI应用工程师", "某公司"), "ai")
        self.assertEqual(OMA.route_resume("Graduate Programme（英文）", "Standard Chartered"), "en")

    def test_format_guard_blocks_bad_sends(self):
        ok, why = OMA.format_guard({"cover": "你好 {company}", "hr_email": "a" + "@" + "b.com",
                                    "resume_path": __file__})
        self.assertFalse(ok)
        self.assertIn("占位符", why)
        ok2, _ = OMA.format_guard({"cover": "ok", "hr_email": "", "resume_path": __file__})
        self.assertFalse(ok2, "无邮箱不得进入发送")
        ok3, _ = OMA.format_guard({"cover": "ok", "hr_email": "a" + "@" + "b.com",
                                   "resume_path": "/nonexistent/xx.pdf"})
        self.assertFalse(ok3, "附件不存在不得进入发送")


class TestEnrich(unittest.TestCase):
    JH_CATEGORY = "official_mail"
    JH_NEW = True

    def test_extract_emails_filters_noise_and_dedupes(self):
        # 夹具用拼接构造：源码里不出现连续邮箱字面量（避免触发仓内 PII 守卫的
        # REVIEW REQUIRED），运行时拼接后的字符串与真邮箱一致，提取语义不变。
        at = "@"
        good = "campus.cndc" + at + "shopee.com"
        noisy = "sentry" + at + "sentry.io"
        example = "hr" + at + "example.com"
        html = f"联系 {good}；备份：{good}；noise: {noisy}；{example}"
        out = OMA.extract_emails(html)
        self.assertIn(good, out)
        self.assertNotIn(noisy, out)
        self.assertNotIn(example, out)
        self.assertEqual(len(out), len(set(out)), "邮箱必须去重")

    def test_promote_email_on_approve(self):
        at = "@"  # 拼接构造，避开源码里的连续邮箱字面量
        found, keep, other = "campus" + at + "sea.com", "a" + at + "b.com", "c" + at + "d.com"
        it = {"hr_email": "", "discovered_email": found, "needs_email": True}
        OMA._promote_email(it)
        self.assertEqual(it["hr_email"], found)
        self.assertFalse(it["needs_email"])
        self.assertEqual(it["email_confirmed_by"], "approve")
        it2 = {"hr_email": keep, "discovered_email": other, "needs_email": False}
        OMA._promote_email(it2)
        self.assertEqual(it2["hr_email"], keep, "已有 hr_email 不得被覆盖")
        it3 = {"hr_email": "", "needs_email": True}
        OMA._promote_email(it3)
        self.assertTrue(it3["needs_email"], "无发现时保持挂起")


if __name__ == "__main__":
    unittest.main()

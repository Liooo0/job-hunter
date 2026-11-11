#!/usr/bin/env python3
"""mail_claim_lint 的单元测试（夹具全部合成，不含真实邮箱/手机号/公司）。

覆盖 2026-10-22 事故的三个具体失败模式：
  1. LICENSE 里的 modify 不能被当成 Dify 命中
  2. 「用Dify搭」这种汉字紧贴的写法必须命中（grep -w / \\b 会漏）
  3. 大小写都要认（dify / DIFY / Dify）
外加：数字归一化、禁用词、sources 私有目录。
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mail_claim_lint as ml  # noqa: E402


class TestTokenBoundary(unittest.TestCase):
    def test_modify_is_not_dify(self):
        rx = ml.token_rx("Dify")
        self.assertIsNone(rx.search("you may modify, merge, publish"))
        self.assertIsNone(rx.search("modifications and derivative works"))

    def test_cjk_adjacent_still_hits(self):
        rx = ml.token_rx("Dify")
        self.assertIsNotNone(rx.search("用Dify搭了一个知识库"))
        self.assertIsNotNone(rx.search("在Dify上做过问答"))
        self.assertIsNotNone(rx.search("Dify工作流编排"))

    def test_case_insensitive(self):
        rx = ml.token_rx("Dify")
        for s in ("dify", "DIFY", "Dify", "DiFy"):
            self.assertIsNotNone(rx.search(f"我用 {s} 做过项目"), s)

    def test_grep_token_skips_license(self):
        with TemporaryDirectory() as td:
            d = Path(td)
            (d / "LICENSE").write_text("Permission is granted to modify and distribute", encoding="utf-8")
            self.assertFalse(ml.grep_token(d, "Dify"))

    def test_grep_token_finds_cjk_adjacent(self):
        with TemporaryDirectory() as td:
            d = Path(td)
            (d / "notes.md").write_text("当年用Dify搭了个客服知识库", encoding="utf-8")
            self.assertTrue(ml.grep_token(d, "Dify"))


class TestNumbers(unittest.TestCase):
    def test_wan_normalized(self):
        self.assertIn("47000", ml.norm_numbers("处理过 4.7 万条记录"))

    def test_phone_and_year_ignored(self):
        nums = ml.norm_numbers("联系 138-0000-0001，2027 届也可以")
        self.assertNotIn("138", nums)
        self.assertNotIn("2027", nums)

    def test_plain_number_kept(self):
        self.assertIn("627", ml.norm_numbers("627 个单元测试"))


class TestLintEntry(unittest.TestCase):
    def _entry(self, cover, sources=None):
        return {"cover": cover, "subject": "测试主题", "sources": sources or []}

    def test_denylist_hits(self):
        # 禁用词表 data/mail_denylist.json 是本地文件（gitignore），fresh clone 上不存在，
        # 所以夹具自带词表并临时指过去，保证 CI（fresh clone）可复现。
        with TemporaryDirectory() as td:
            orig = ml.DENYLIST
            ml.DENYLIST = Path(td) / "denylist.json"
            ml.DENYLIST.write_text(json.dumps({"words": ["屏幕级"]}), encoding="utf-8")
            try:
                e = self._entry("我做的是屏幕级自动化")
                codes = [c for _, _, c in ml.lint_entry(e)]
                self.assertIn("DENYLIST", codes)
            finally:
                ml.DENYLIST = orig

    def test_private_source_satisfies_token(self):
        with TemporaryDirectory() as td:
            d = Path(td)
            (d / "app.py").write_text("import chromadb\n", encoding="utf-8")
            e = self._entry("用 Chroma 做向量库",
                            [{"kind": "private", "path": td, "desc": "合成项目"}])
            codes = [c for _, _, c in ml.lint_entry(e)]
            self.assertNotIn("TOKEN_NO_EVIDENCE", codes)

    def test_missing_evidence_fails(self):
        with TemporaryDirectory() as td:
            d = Path(td)
            (d / "app.py").write_text("print('hello')\n", encoding="utf-8")
            e = self._entry("我是 Dify 的实际使用者",
                            [{"kind": "private", "path": td, "desc": "合成项目"}])
            codes = [c for _, _, c in ml.lint_entry(e)]
            self.assertIn("TOKEN_NO_EVIDENCE", codes)

    def test_cloud_declaration_downgrades_to_warn(self):
        e = self._entry("我用 Dify 搭过知识库",
                        [{"kind": "cloud", "desc": "Dify 工作区可演示"}])
        probs = ml.lint_entry(e)
        self.assertTrue(all(lvl != "FAIL" for lvl, _, _ in probs))
        self.assertIn("CLOUD_ONLY", [c for _, _, c in probs])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""history 路径扫描回归：中文文件名 / git quotepath 转义不得漏报。"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from scripts.public_repo_guard import GuardEngine  # noqa: E402


class TestUnquoteGitPath(unittest.TestCase):
    def test_plain_path_untouched(self):
        self.assertEqual(GuardEngine._unquote_git_path("src/a.py"), "src/a.py")

    def test_c_quoted_utf8(self):
        # git 默认 core.quotepath=true 时的中文路径形态（深圳 = e6 b7 b1 e5 9c b3）
        quoted = '"boss-\\346\\267\\261\\345\\234\\263-log.json"'
        self.assertEqual(GuardEngine._unquote_git_path(quoted), "boss-深圳-log.json")

    def test_mixed_ascii_and_escape(self):
        quoted = '"dir/\\346\\227\\245\\345\\277\\227.log"'
        self.assertEqual(GuardEngine._unquote_git_path(quoted), "dir/日志.log")


class TestHistoryProhibitedPathScan(unittest.TestCase):
    """历史添加后删除的违规路径必须被抓到（L3）。"""

    def _build_repo(self, filename: str) -> tempfile.TemporaryDirectory:
        td = tempfile.TemporaryDirectory()
        repo = Path(td.name)
        for cmd in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.check_call(cmd, cwd=repo)
        (repo / filename).write_text('{"a":1}', encoding="utf-8")
        subprocess.check_call(["git", "add", "."], cwd=repo)
        subprocess.check_call(["git", "commit", "-qm", "add"], cwd=repo)
        (repo / filename).unlink()
        subprocess.check_call(["git", "add", "-A"], cwd=repo)
        subprocess.check_call(["git", "commit", "-qm", "rm"], cwd=repo)
        return td

    def _history_findings(self, repo: Path):
        engine = GuardEngine(str(repo))
        engine.scan_history()
        return [
            f for f in engine.findings
            if getattr(f, "category", "") == "PROHIBITED_PATH"
        ]

    def test_ascii_log_name_caught_after_delete(self):
        td = self._build_repo("boss-shenzhen-log.json")
        try:
            hits = self._history_findings(Path(td.name))
            self.assertTrue(hits, "ASCII 违规路径历史添加后删除，必须被 L3 抓到")
            self.assertIn("boss-shenzhen-log.json", hits[0].target)
        finally:
            td.cleanup()

    def test_chinese_log_name_caught_after_delete(self):
        td = self._build_repo("boss-深圳-log.json")
        try:
            hits = self._history_findings(Path(td.name))
            self.assertTrue(
                hits,
                "中文违规路径（git 可能 C-quoting）历史添加后删除，必须被 L3 抓到",
            )
            self.assertIn("深圳", hits[0].target + hits[0].evidence)
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()

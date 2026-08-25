#!/usr/bin/env python3
"""report.py 渲染加固单元测试（P2-T1）。

验证点：
1. 公司名/岗位名/原因含 <>&" 时被 html.escape，页面结构不被破坏；
2. 输出无残留模板占位符（$xxx / {{ }} / {% %}）；
3. 空数据分支输出「暂无投递记录」，不出现空区块标签。

跑法一（unittest discover，从项目根目录）:
    PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -t . -v

跑法二（直接运行）:
    cd tests && PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 test_report.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import report  # noqa: E402
from shared import format_salary  # noqa: E402


def _fake_merged():
    nasty = '<b>A&Q"测试</b> <script>alert(1)</script>'
    return {
        "applied": [
            {"company": nasty, "job": '岗位"X"&<Y>', "salary": "15-25K·13薪",
             "score": 66, "status": "SENT", "_log_file": "boss-深圳-log"},
            {"company": "正常公司", "job": "测试开发工程师", "salary": "",
             "score": 20, "status": "UNCERTAIN", "_log_file": "boss-北京-log"},
        ],
        "skipped": [
            {"job": "<i>跳过岗</i>", "reason": '标题包含排除词: "总监"', "score": 0},
        ],
        "failed": [{"job": "失败岗", "error": "timeout"}],
    }


def _fake_trend():
    return [{"date": "2026-08-25", "applied": 3, "skipped": 1, "failed": 0}]


class TestReportRendering(unittest.TestCase):
    def setUp(self):
        self._orig = (report.load_config, report.merge_logs, report.recent_activity)
        report.merge_logs = lambda skill_dir=None: _fake_merged()
        report.recent_activity = lambda skill_dir=None, days=7: _fake_trend()
        report.load_config = lambda skill_dir=None: {"report_dir": "data/reports"}

    def tearDown(self):
        report.load_config, report.merge_logs, report.recent_activity = self._orig

    def test_generate_html_escapes_dynamic_fields(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.html"
            report.generate_html(skill_dir=PROJECT, output_path=out)
            html = out.read_text(encoding="utf-8")

        # 动态文本被转义：原始危险串不得原样出现在 HTML 里
        self.assertNotIn("<script>", html)
        self.assertNotIn('<b>A&', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp;", html)
        # 页面骨架完好（没有被注入的标签截断）
        self.assertTrue(html.rstrip().endswith("</html>"))
        self.assertEqual(html.count("<table>"), 2)  # 投递表 + 跳过表

    def test_no_leftover_placeholders(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.html"
            report.generate_html(skill_dir=PROJECT, output_path=out)
            html = out.read_text(encoding="utf-8")
        for token in ("{{", "}}", "{%", "$applied_section", "$trend_section",
                      "$skipped_section", "$report_time"):
            self.assertNotIn(token, html)

    def test_empty_data_branch(self):
        empty = {"applied": [], "skipped": [], "failed": []}
        report.merge_logs = lambda skill_dir=None: empty
        report.recent_activity = lambda skill_dir=None, days=7: []
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.html"
            report.generate_html(skill_dir=PROJECT, output_path=out)
            html = out.read_text(encoding="utf-8")
        self.assertIn("暂无投递记录", html)
        self.assertNotIn("所有投递记录</h2>", html)

    def test_format_salary_keeps_pua_decoded(self):
        self.assertEqual(format_salary("15-25K·13薪"), "15-25K·13薪")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分类回归看板（任务书 §5.1）。

跑法（与 §2 的全量回归同源，只是多打一块分类看板）：
    env PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc \
        /usr/bin/python3 tests/run_regression.py

为什么要这个脚本：只打一行「Ran N tests OK」看不出**哪一类规则**退化了 ——
新增用例会把某条老规则的回归淹没在总数里。所以：
  · 每个规则类别各占一行，单独统计通过/失败；
  · 「新增回归」与「历史综合回归」**分开成行**，历史基线永远不被新用例掩盖。

分类靠测试类上的类属性打标（见 tests/test_rules_convergence.py 顶部说明）：
    JH_CATEGORY = "cohort"  → 归到哪一行
    JH_NEW = True           → 计入「新增回归」，否则计入「历史综合回归」
没打标的测试类一律算历史基线。
"""
import sys
import unicodedata
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# ── 分类顺序与显示名（照 §5.1 的看板格式）──
CATEGORIES = [
    ("cohort",           "届别闸 (Cohort Filter)"),
    ("part_time",        "兼职过滤 (Part-time Filter)"),
    ("schedule_inquiry", "制度问询状态机 (Schedule Inquiry)"),
    ("line_isolation",   "双线隔离 (Line Isolation)"),
    ("transition_idle",  "过渡线取舍 (Transition Trade)"),
    ("wording",          "技术措辞对齐 (Wording Consistency)"),
    ("greeting",         "招呼语 (Greeting Templates)"),
    ("rate_gates",       "频率与风控闸 (Rate Gates)"),
    ("liepin",           "猎聘熔断 (Liepin Bypass)"),
    ("salary_ceiling",   "30K 薪资上限 (Salary Ceiling)"),
    ("boss_last_mile",   "Boss 最后一公里 (Boss Last Mile)"),
    ("skip_reason",      "跳过原因埋点 (Skip Reason)"),
]
_LABEL_W = 39
_SEP = "=" * 48
_THIN = "-" * 48


class DashboardResult(unittest.TextTestResult):
    """按类别累加通过/失败数。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.by_cat = {k: [0, 0] for k, _ in CATEGORIES}   # [passed, failed]
        self.new = [0, 0]
        self.legacy = [0, 0]

    def _bump(self, test, ok: bool):
        cls = test.__class__
        cat = getattr(cls, "JH_CATEGORY", None)
        if cat in self.by_cat:
            self.by_cat[cat][0 if ok else 1] += 1
        (self.new if getattr(cls, "JH_NEW", False) else self.legacy)[0 if ok else 1] += 1

    # 故意不调 super().addSuccess —— 默认的「每例一个点」会把看板刷得没法看。
    # 失败/异常仍走父类，traceback 照常打印。
    def addSuccess(self, test):
        self._bump(test, True)

    def addFailure(self, test, err):
        self._bump(test, False)
        super().addFailure(test, err)

    def addError(self, test, err):
        self._bump(test, False)
        super().addError(test, err)


def _disp_len(s: str) -> int:
    """按**终端显示宽度**算长度：CJK 全角字符占两列，len() 会把看板整歪。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(label: str, width: int) -> str:
    return label + " " * max(1, width - _disp_len(label))


def _row(label, passed, failed):
    return f"{_pad(label, _LABEL_W)}: [{passed}] passed, {failed} failed"


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(str(BASE / "tests"), pattern="test_*.py",
                            top_level_dir=str(BASE))
    runner = unittest.TextTestRunner(verbosity=1, resultclass=DashboardResult)
    result = runner.run(suite)

    skipped = len(result.skipped)
    total_p = result.new[0] + result.legacy[0]
    total_f = result.new[1] + result.legacy[1]

    print()
    print(_SEP)
    print("          Job Hunter Regression Report")
    print(_SEP)
    for key, label in CATEGORIES:
        p, f = result.by_cat[key]
        print(_row(label, p, f))
    print(_THIN)
    print(_row("新增回归 (NEW REGRESSION)", result.new[0], result.new[1]))
    print(_row("历史综合回归 (LEGACY BASELINE)", result.legacy[0], result.legacy[1])
          + "   ← 必须单独统计，不得被新增用例掩盖")
    print(_THIN)
    print(_row("TOTAL", total_p, total_f))
    print(_SEP)
    if skipped:
        print(f"⚠️ 有 {skipped} 个用例被跳过（§0.3 不允许靠跳过凑绿）")
    ok = not result.failures and not result.errors
    print("✅ 全绿" if ok else "❌ 有失败，见上方 traceback")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

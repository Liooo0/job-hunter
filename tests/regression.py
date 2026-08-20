#!/usr/bin/env python3
"""回归测试 v2 — 用真实 JD 案例验证筛选规则，防假阳/假阴。

用法（从 job-hunter 项目目录）:
    PYTHONPYCACHEPREFIX=/tmp/jh_pyc python3 tests/regression.py

测试集: tests/regression_cases.json（真实案例：pass/reject）
"""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT))
from deep_filter import deep_filter  # noqa: E402
from shared import score_jd, smart_filter, load_config  # noqa: E402


def classify(title: str, desc: str, company: str, salary: str, cfg: dict) -> str:
    """三层过滤：score_jd(标题排除) → smart_filter(正文/薪资) → deep_filter(标题党/包装/背调)"""
    s, r = score_jd(title, desc, cfg)
    if s == 0:
        return "reject"
    s2, r2 = smart_filter(company, title, desc, salary, s, cfg, city="深圳")
    if s2 == 0:
        return "reject"
    s3, r3 = deep_filter(company, title, desc, salary, s2)
    return "reject" if s3 == 0 else "pass"


def main():
    cases = json.loads((PROJECT / "tests/regression_cases.json").read_text())["cases"]
    cfg = load_config()
    passed, failed = 0, []
    for c in cases:
        result = classify(c["title"], c["desc"], c["company"], c["salary"], cfg)
        ok = result == c["expect"]
        if ok:
            passed += 1
        else:
            failed.append(c["id"])
            print(f"  ❌ {c['id']}: 期望{c['expect']} 实际{result} | {c['title']} | {c.get('note','')}")
    print(f"\n{'='*50}")
    print(f"回归测试: {passed}/{len(cases)} 通过")
    for fid in failed:
        print(f"  FAIL: {fid}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

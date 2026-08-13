#!/usr/bin/env python3
"""S5 失败原因追踪 — 测试套件（任务书要求的 9 项）"""
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

from failure_tracking import (
    record_failure, get_failure_reasons, get_failure_stats,
    get_failure_funnel, get_resume_experiment_summary,
    FAILURE_STAGES, FAILURE_REASONS, DB,
)

passed, failed = 0, 0


def assert_result(cond, msg):
    if not cond:
        raise AssertionError(msg)


def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  ✅ {name}")
    except Exception as e:
        failed += 1
        print(f"  ❌ {name}: {e}")


def reset_db():
    # 先确保表存在（复用 SCHEMA）
    from failure_tracking import _get_conn
    _get_conn().close()
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM failure_reasons")
    conn.commit()
    conn.close()


def get_first_job():
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT id, company, job_title, resume_version FROM applications WHERE resume_version IS NOT NULL AND resume_version != '' LIMIT 1"
    ).fetchone()
    conn.close()
    return row  # (id, company, job_title, resume_version)


print("S5 测试开始\n")

# 拿一个真实 job_id
job = get_first_job()
if job is None:
    print("❌ applications 表无带 resume_version 的记录，无法测试")
    sys.exit(1)
job_id, company, title, rv = job
print(f"  测试用 job: id={job_id}, {company} {title} (rv={rv})\n")

reset_db()

# 1. 正常记录失败原因
t("正常记录失败原因", lambda: (
    record_failure(str(job_id), "resume_sent", "resume_rejected", resume_version=rv, note="测试"),
    assert_result(len(get_failure_reasons(str(job_id))) == 1, "应有一条记录"),
))

# 2. 非法 failure_reason 被拒绝
def test_invalid_reason():
    try:
        record_failure(str(job_id), "resume_sent", "NOT_A_REASON")
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass
t("非法 failure_reason 被拒绝", test_invalid_reason)

# 3. 非法 failure_stage 被拒绝
def test_invalid_stage():
    try:
        record_failure(str(job_id), "NOT_A_STAGE", "resume_rejected")
        raise AssertionError("应抛 ValueError")
    except ValueError:
        pass
t("非法 failure_stage 被拒绝", test_invalid_stage)

# 4. 重复记录不产生重复数据
t("重复记录不产生重复数据", lambda: (
    record_failure(str(job_id), "resume_sent", "resume_rejected", resume_version=rv),
    assert_result(len(get_failure_reasons(str(job_id))) == 1, "重复记录后仍应只有1条"),
))

# 5. resume_version 正确进入统计
t("resume_version 正确进入统计", lambda: (
    assert_result(any(x["resume_version"] == rv for x in get_failure_reasons()), "统计应含 resume_version"),
))

# 6. 三线简历能分别统计
def test_three_versions():
    # 给三个版本各插一条
    for v in ["A-AI应用", "B-解决方案", "C-车联网"]:
        # 找对应版本的 job
        conn = sqlite3.connect(DB)
        row = conn.execute(
            "SELECT id FROM applications WHERE resume_version=? LIMIT 1", (v,)
        ).fetchone()
        conn.close()
        if row:
            record_failure(str(row[0]), "viewed", "viewed_no_reply", resume_version=v)
    summary = get_resume_experiment_summary("2026-08-14", "2026-08-20")
    versions = {s["resume_version"] for s in summary}
    assert_result("A-AI应用" in versions and "B-解决方案" in versions and "C-车联网" in versions,
                  "三线应都在 summary 中")
t("三线简历能分别统计", test_three_versions)

# 7. failure_reason 按日期筛选
def test_date_filter():
    # 全量
    all_cnt = len(get_failure_stats())
    # 未来日期（应无结果）
    future = get_failure_stats(start_date="2099-01-01", end_date="2099-12-31")
    assert_result(all_cnt >= 1 and len(future) == 0, f"日期筛选异常（全量{all_cnt}，未来{len(future)}）")
t("failure_reason 按日期筛选", test_date_filter)

# 8. 旧数据仍能正常读取（applications 表不受影响）
def test_old_data():
    conn = sqlite3.connect(DB)
    n = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    conn.close()
    assert_result(n >= 5000, f"applications 表应仍有大量旧数据（实际{n}）")
t("旧数据仍能正常读取", test_old_data)

# 9. 枚举完整性
def test_enums():
    assert_result(len(FAILURE_STAGES) == 9, f"stage 枚举应为9个（实际{len(FAILURE_STAGES)}）")
    assert_result(len(FAILURE_REASONS) == 23, f"reason 枚举应为23个（实际{len(FAILURE_REASONS)}）")
t("枚举完整性", test_enums)


print(f"\n{'='*50}")
print(f"结果: {passed} 通过 / {failed} 失败")
sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""P0 正确性测试：B1 高薪实习 / B3 趋势按 entry.time / SQLite 单一事实源 / 公司名规范化。

用法（从 job-hunter 项目目录）:
    PYTHONPYCACHEPREFIX=/tmp/jh_pyc python3 tests/test_p0.py
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import shared  # noqa: E402
import store  # noqa: E402

passed, failed = 0, []


def t(name, fn):
    global passed
    try:
        fn()
        passed += 1
        print(f"  ✅ {name}")
    except Exception as e:
        failed.append(name)
        print(f"  ❌ {name}: {e}")


def minimal_cfg():
    return {
        "exclude_company_keywords": [],
        "exclude_keywords": [],
        "body_exclude_keywords": [],
        "salary_filter": {
            "home_cities": ["深圳"],
            "home_min_accept": 6,
            "away_min_accept": 10,
        },
    }


# ── B1：smart_filter 高薪实习 ──
def test_high_salary_intern_boosted():
    cfg = minimal_cfg()
    score, reason = shared.smart_filter(
        "深圳市XX科技有限公司", "AI应用工程师实习",
        "负责AI应用开发，实习560-600元/天，转正后12-16K",
        "10-15K", 40, cfg, city="深圳",
    )
    assert score >= 60, f"高薪实习应保底60，实际 {score}"
    assert "高薪实习" in reason, f"原因应包含高薪实习，实际 {reason!r}"


def test_high_salary_intern_does_not_lower_higher_score():
    cfg = minimal_cfg()
    score, _ = shared.smart_filter(
        "某公司", "Coze实习生", "实习560-600元/天，Agent工作流开发",
        "10-12K", 80, cfg, city="深圳",
    )
    assert score >= 80, f"已有更高分不应被降低，实际 {score}"


def test_low_salary_intern_still_rejected():
    cfg = minimal_cfg()
    score, reason = shared.smart_filter(
        "某公司", "AI实习生", "实习期120元/天，负责数据整理",
        "3-4K", 30, cfg, city="深圳",
    )
    assert score == 0, f"低薪实习应被过滤，实际 {score} {reason}"


def test_smart_filter_no_nameerror_on_plain_desc():
    cfg = minimal_cfg()
    score, _ = shared.smart_filter(
        "某公司", "AI应用工程师", "负责AI应用开发，Python+FastAPI",
        "15-25K", 70, cfg, city="深圳",
    )
    assert score == 70


# ── B3：recent_activity 按 entry.time 聚合 ──
def test_recent_activity_uses_entry_time_not_mtime():
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp)
        old_db, old_project = store.DB, store.PROJECT
        store.DB = Path(tmp) / "test.db"
        store.PROJECT = Path(tmp)
        try:
            log_file = skill_dir / "boss-深圳-log.json"
            today = datetime.now().isoformat()
            old_day = (datetime.now() - timedelta(days=9)).isoformat()
            two_days = (datetime.now() - timedelta(days=2)).isoformat()
            log = {
                "applied": [
                    {"job": "A", "time": today},
                    {"job": "B", "time": old_day},   # 超过7天，应被排除
                    {"job": "C", "time": two_days},
                ],
                "skipped": [
                    {"job": "D", "time": today},
                ],
                "failed": [],
            }
            log_file.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            # 把 mtime 改成今天，验证统计不再被 mtime 污染
            now = datetime.now().timestamp()
            os.utime(log_file, (now, now))

            rows = shared.recent_activity(skill_dir=skill_dir, days=7)
            by_date = {r["date"]: r for r in rows}
            assert today[:10] in by_date
            assert two_days[:10] in by_date
            assert by_date[today[:10]]["applied"] == 1, by_date
            assert by_date[two_days[:10]]["applied"] == 1, by_date
            assert old_day[:10] not in by_date, "超过7天的旧记录不应计入"
            assert by_date[today[:10]]["skipped"] == 1
        finally:
            store.DB, store.PROJECT = old_db, old_project


# ── 公司名规范化 ──
def test_normalize_company():
    assert store.normalize_company("深圳市XX科技有限公司") == "xx科技"
    assert store.normalize_company("XX科技（深圳）有限公司") == "xx科技"
    assert store.normalize_company("北京YY科技有限公司") == "yy科技"
    assert store.normalize_company("") == ""


# ── SQLite 单一事实源 ──
def test_store_record_merge_and_counts():
    with tempfile.TemporaryDirectory() as tmp:
        old_db, old_project = store.DB, store.PROJECT
        store.DB = Path(tmp) / "test.db"
        store.PROJECT = Path(tmp)
        try:
            now = datetime.now()
            store.record_application(
                platform="boss", city="深圳", company="深圳市XX科技有限公司", title="AI应用工程师",
                salary="15-20K", keyword="AI应用工程师", score=75, resume_version="A-AI应用",
                decision="applied", status="APPLIED", verified=1, applied_at=now.isoformat(),
                event_type="applied",
            )
            store.record_application(
                platform="boss", city="深圳", company="YY科技有限公司", title="AI实施工程师",
                salary="8-12K", keyword="AI实施", score=70,
                decision="uncertain", status="UNCERTAIN", verified=0, applied_at=now.isoformat(),
                event_type="uncertain",
            )
            store.record_application(
                platform="boss", city="广州", company="ZZ科技", title="销售岗",
                salary="6-8K", keyword="AI运营", score=20,
                decision="skipped", status="SKIPPED", reason="销售包装",
                applied_at=now.isoformat(), event_type="skipped",
            )
            store.record_application(
                platform="boss", city="深圳", company="QQ科技", title="测试岗",
                salary="8-10K", keyword="测试", score=50,
                decision="failed", status="FAILED", reason="异常:会话未打开",
                applied_at=now.isoformat(), event_type="failed", event_error="trace...",
            )

            merged = store.merge_records()
            assert len(merged["applied"]) == 2, merged["applied"]
            assert len(merged["skipped"]) == 1
            assert len(merged["failed"]) == 1
            assert merged["applied"][0]["status"] in ("APPLIED", "UNCERTAIN")

            start = now.strftime("%Y-%m-%dT00:00:00")
            assert store.count_applied_since(start) == 2
            assert store.count_applied_since(start, include_uncertain=False) == 1

            assert store.company_applied_recently("深圳", "深圳市XX科技有限公司", 7)
            assert store.company_applied_recently("深圳", "XX科技", 7)
            assert not store.company_applied_recently("深圳", "完全不存在的公司", 7)

            days = store.recent_activity_days(days=7)
            assert len(days) == 1
            assert days[0]["applied"] == 2
            assert days[0]["skipped"] == 1
            assert days[0]["failed"] == 1

            # events 应该写了 4 条
            conn = sqlite3.connect(store.DB)
            n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.close()
            assert n == 4
        finally:
            store.DB, store.PROJECT = old_db, old_project


def test_store_migrate_legacy_logs():
    with tempfile.TemporaryDirectory() as tmp:
        old_db, old_project = store.DB, store.PROJECT
        store.DB = Path(tmp) / "test.db"
        store.PROJECT = Path(tmp)
        try:
            (Path(tmp) / "boss-深圳-log.json").write_text(json.dumps({
                "applied": [
                    {"company": "深圳市XX科技有限公司", "job": "AI应用工程师",
                     "salary": "15-20K", "score": 75, "city": "深圳",
                     "keyword": "AI应用工程师", "time": "2026-08-18T10:00:00"},
                ],
                "skipped": [],
                "failed": [],
            }, ensure_ascii=False), encoding="utf-8")
            counts = store.migrate_legacy_logs(verbose=False)
            assert counts["inserted"] == 1
            # 幂等：再跑一次不新增
            counts2 = store.migrate_legacy_logs(verbose=False)
            assert counts2["inserted"] == 0
            assert store.count_applied_since("2026-01-01T00:00:00") == 1
        finally:
            store.DB, store.PROJECT = old_db, old_project


def main():
    print("P0 测试开始\n")
    t("B1: 高薪实习(正文日薪) 保底60+原因", test_high_salary_intern_boosted)
    t("B1: 已有更高分不被高薪实习逻辑降低", test_high_salary_intern_does_not_lower_higher_score)
    t("B1: 低薪实习仍被过滤", test_low_salary_intern_still_rejected)
    t("B1: 普通岗位 smart_filter 不触发 NameError", test_smart_filter_no_nameerror_on_plain_desc)
    t("B3: recent_activity 按 entry.time 聚合", test_recent_activity_uses_entry_time_not_mtime)
    t("A9: 公司名规范化", test_normalize_company)
    t("store: 记录/合并/熔断计数/去重/事件", test_store_record_merge_and_counts)
    t("store: 旧 JSON 幂等迁移", test_store_migrate_legacy_logs)
    print(f"\n{'='*50}")
    print(f"P0 测试: {passed} 通过 / {len(failed)} 失败")
    for name in failed:
        print(f"  FAIL: {name}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

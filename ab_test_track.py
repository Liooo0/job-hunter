#!/usr/bin/env python3
"""7天 A/B 实验漏斗系统 v2 — 2026-08-11 ~ 08-16 正式实验

设计（2026-08-10 用户定稿，5 项修正全部采纳）：
1. 岗位池占比 = 目标分布非硬配额（先过硬过滤，再统计实际分布）
2. 城市 = 优先级非配额（primary/secondary/opportunistic，避免自证）
3. 漏斗拆 7 段：投递 → 已读 → 回复 → 面试 → 技术面 → Offer（已读率是诊断第一屏的关键）
4. 拒绝原因分类（NO_READ/READ_NO_REPLY/REJECT_*）
5. 裁决指标层级：面试率(一级) > 有效回复率(二级) > 已读率(三级) > Offer率(四级)
6. 2026-08-10 的 22 投 = Baseline-0 预实验，不混入正式实验

数据库：ab_experiment.db
- applications: 投递记录（含 category/city_priority/read/replied/interview/offer/reject_reason/resume_version）
- 已读/回复状态需人工补录（Boss 网页端状态）或由 hr 消息扫描脚本回填

用法：
  python3 ab_test_track.py                  # 查看每日漏斗
  python3 ab_test_track.py --import-logs    # 从 boss-*-log.json 导入投递（幂等）
  python3 ab_test_track.py --update 公司名 岗位 read|replied|interview|offer   # 补录状态
  python3 ab_test_track.py --reject 公司名 岗位 REJECT_LOW_EXPERIENCE          # 记录拒绝原因
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path("/Users/REDACTED/projects/job-hunter")
DB = PROJECT / "ab_experiment.db"

BASELINE_DAY = date(2026, 8, 10)   # 预实验（不参与裁决）
EXPERIMENT_START = date(2026, 8, 11)
EXPERIMENT_END = date(2026, 8, 16)

POOL_KEYWORDS = {
    "AI应用/AI工程师": ["AI应用工程师", "AI工程师", "LLM应用", "大模型应用", "Agent应用", "智能体开发"],
    "AI实施/工作流/RAG/Dify": ["AI实施", "AI实施工程师", "工作流工程师", "RAG", "知识库运营", "Dify", "Coze", "AI解决方案"],
    "RPA/Python自动化": ["RPA开发", "Python开发", "自动化", "数字员工", "低代码开发", "AI自动化"],
    "技术支持/AI产品": ["AI技术支持", "AI产品", "AI运营", "AI训练师", "AI文档"],
}

CITY_PRIORITY = {
    "深圳": "primary",
    "广州": "secondary", "东莞": "secondary", "佛山": "secondary",
    "北京": "opportunistic", "上海": "opportunistic", "杭州": "opportunistic", "成都": "opportunistic",
}

REJECT_REASONS = [
    "NO_READ", "READ_NO_REPLY", "REJECT_LOW_EXPERIENCE", "REJECT_EDUCATION",
    "REJECT_SALARY", "REJECT_CITY", "REJECT_SKILL", "REJECT_JOB_TYPE", "HR主动联系", "进入面试",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    company TEXT,
    job_title TEXT,
    city TEXT,
    salary TEXT,
    category TEXT,
    city_priority TEXT,
    jd_score INTEGER,
    deep_filter_result TEXT,
    manual_review TEXT,
    applied INTEGER DEFAULT 1,
    read INTEGER DEFAULT 0,
    replied INTEGER DEFAULT 0,
    interview INTEGER DEFAULT 0,
    technical_interview INTEGER DEFAULT 0,
    offer INTEGER DEFAULT 0,
    reject_reason TEXT,
    resume_version TEXT DEFAULT 'v2.1',
    opening_message_version TEXT DEFAULT 'v1',
    is_baseline INTEGER DEFAULT 0,
    UNIQUE(day, company, job_title)
);
"""


def get_db():
    conn = sqlite3.connect(DB)
    conn.execute(SCHEMA)
    return conn


def pool_of(keyword: str) -> str:
    for pool, kws in POOL_KEYWORDS.items():
        if keyword in kws:
            return pool
    return "其他"


def import_logs():
    conn = get_db()
    inserted = 0
    for f in sorted(PROJECT.glob("boss-*-log.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for e in d.get("applied", []):
            day = (e.get("time") or "")[:10]
            if not day:
                continue
            try:
                d_ = date.fromisoformat(day)
            except ValueError:
                continue
            company = e.get("company", "") or "?"
            job = e.get("job", "") or "?"
            city = e.get("city", "") or "?"
            keyword = e.get("keyword", "") or ""
            is_base = 1 if d_ == BASELINE_DAY else 0
            conn.execute(
                """INSERT OR IGNORE INTO applications
                   (day, company, job_title, city, salary, category, city_priority, jd_score, is_baseline)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (day, company, job, city, e.get("salary", ""), pool_of(keyword),
                 CITY_PRIORITY.get(city, "other"), e.get("score", 0), is_base),
            )
            inserted += 1
    conn.commit()
    conn.close()
    print(f"✅ 导入完成（含已存在跳过），新增/更新 {inserted} 条候选")


def update_status(company: str, job: str, field: str, value: int = 1):
    valid = {"read", "replied", "interview", "technical_interview", "offer"}
    if field not in valid:
        print(f"❌ 字段必须是 {valid}")
        return
    conn = get_db()
    cur = conn.execute(
        f"UPDATE applications SET {field}=? WHERE company LIKE ? AND job_title LIKE ?",
        (value, f"%{company}%", f"%{job}%"),
    )
    conn.commit()
    print(f"✅ 更新 {cur.rowcount} 条: {company} {job} → {field}={value}")
    conn.close()


def set_reject(company: str, job: str, reason: str):
    if reason not in REJECT_REASONS:
        print(f"❌ 原因必须是: {REJECT_REASONS}")
        return
    conn = get_db()
    cur = conn.execute(
        "UPDATE applications SET reject_reason=? WHERE company LIKE ? AND job_title LIKE ?",
        (reason, f"%{company}%", f"%{job}%"),
    )
    conn.commit()
    print(f"✅ 记录拒绝原因 {cur.rowcount} 条: {company} {job} → {reason}")
    conn.close()


def show_funnel():
    conn = get_db()
    rows = conn.execute("SELECT * FROM applications ORDER BY day").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM applications LIMIT 1").description]
    conn.close()

    print("=" * 70)
    print("7天 A/B 实验漏斗（正式实验 08-11 ~ 08-16，Baseline=08-10 不参与裁决）")
    print("=" * 70)

    # 按天（只显示 08-10 起，历史数据不展示）
    by_day = defaultdict(list)
    for r in rows:
        try:
            d_ = date.fromisoformat(r[cols.index("day")])
        except ValueError:
            continue
        if d_ < BASELINE_DAY:
            continue
        by_day[r[cols.index("day")]].append(dict(zip(cols, r)))

    for day in sorted(by_day):
        rs = by_day[day]
        is_base = rs[0]["is_baseline"] == 1
        tag = " [Baseline-0 预实验]" if is_base else ""
        print(f"\n📅 {day}{tag}: {len(rs)} 投")
        applied = len(rs)
        read = sum(1 for r in rs if r["read"])
        replied = sum(1 for r in rs if r["replied"])
        interview = sum(1 for r in rs if r["interview"])
        tech = sum(1 for r in rs if r["technical_interview"])
        offer = sum(1 for r in rs if r["offer"])
        print(f"   投递 {applied} → 已读 {read} → 回复 {replied} → 面试 {interview} → 技术面 {tech} → Offer {offer}")
        if applied:
            print(f"   已读率 {read*100//applied}% | 回复率 {replied*100//applied}% | 面试率 {interview*100//applied}%")
        # 岗位池分布
        by_pool = defaultdict(int)
        by_prio = defaultdict(int)
        for r in rs:
            by_pool[r["category"]] += 1
            by_prio[r["city_priority"]] += 1
        print(f"   岗位池: {dict(by_pool)}")
        print(f"   城市优先级: {dict(by_prio)}")

    # 正式实验累计
    exp = [dict(zip(cols, r)) for r in rows if r[cols.index("day")] and EXPERIMENT_START <= date.fromisoformat(r[cols.index("day")]) <= EXPERIMENT_END]
    if exp:
        applied = len(exp)
        read = sum(1 for r in exp if r["read"])
        replied = sum(1 for r in exp if r["replied"])
        interview = sum(1 for r in exp if r["interview"])
        tech = sum(1 for r in exp if r["technical_interview"])
        offer = sum(1 for r in exp if r["offer"])
        print("\n" + "=" * 70)
        print(f"📊 正式实验累计（{EXPERIMENT_START}~{EXPERIMENT_END}）: {applied} 投")
        print(f"   已读率 {read*100//applied}% | 有效回复率 {replied*100//applied}% | 面试率 {interview*100//applied}% | Offer率 {offer*100//applied}%")
        print("   裁决层级: 面试率(一级) > 有效回复率(二级) > 已读率(三级) > Offer率(四级)")
        # 拒绝原因分布
        reasons = defaultdict(int)
        for r in exp:
            if r["reject_reason"]:
                reasons[r["reject_reason"]] += 1
        if reasons:
            print(f"   拒绝原因: {dict(reasons)}")


def main():
    ap = argparse.ArgumentParser(description="7天 A/B 实验漏斗")
    ap.add_argument("--import-logs", action="store_true", help="从 boss 日志导入投递")
    ap.add_argument("--update", nargs=3, metavar=("公司", "岗位", "字段"), help="补录状态 read/replied/interview/offer")
    ap.add_argument("--reject", nargs=3, metavar=("公司", "岗位", "原因"), help="记录拒绝原因")
    args = ap.parse_args()

    if args.import_logs:
        import_logs()
    elif args.update:
        update_status(*args.update)
    elif args.reject:
        set_reject(*args.reject)
    else:
        show_funnel()


if __name__ == "__main__":
    main()

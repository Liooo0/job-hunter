"""S5 — 失败原因数据层（Failure Reason Tracking）

服务 8/14~8/20 三线简历实验。只提供数据模型 + 记录接口 + 统计接口，
不碰投递主流程，不实现完整 HR 状态机。

设计原则（严格区分三概念）：
    status          = 当前招聘状态（applications 表已有字段）
    failure_stage   = 失败发生在哪个阶段（固定枚举）
    failure_reason  = 为什么失败（固定枚举）
不要把"简历拒绝"直接写成 status。

数据落点：复用 ab_experiment.db（SQLite），新建 failure_reasons 表。
不新增平行数据库。

用法（CLI）:
    python3 failure_tracking.py record <job_id> <stage> <reason> [note]   # 记录失败原因
    python3 failure_tracking.py stats [--resume A|B|C] [--days N]          # 统计
    python3 failure_tracking.py funnel [--start 2026-08-14] [--end 2026-08-20]
    python3 failure_tracking.py experiment [--start ...] [--end ...]        # 三线简历实验汇总
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).parent
DB = PROJECT / "ab_experiment.db"

# ── 固定枚举 ──
FAILURE_STAGES = [
    "apply",           # 投递阶段
    "viewed",          # 查看阶段
    "contacted",       # HR 联系阶段
    "replied",         # 已回复
    "resume_sent",     # 简历已发送
    "interview_1",     # 一面
    "interview_2",     # 二面
    "offer",           # Offer 阶段
    "closed",          # 关闭/终止
]

FAILURE_REASONS = [
    # 投递阶段
    "apply_failed", "duplicate", "position_closed", "invalid_position",
    # 查看阶段
    "viewed_no_reply", "viewed_timeout",
    # HR 联系阶段
    "contact_no_reply", "contact_rejected",
    # 简历阶段
    "resume_rejected", "resume_no_reply",
    # 面试阶段
    "interview_1_rejected", "interview_2_rejected",
    "technical_gap", "experience_gap", "salary_mismatch", "culture_mismatch",
    # Offer 阶段
    "offer_rejected", "offer_salary_low", "offer_other",
    # 其他
    "candidate_withdrew", "company_withdrew", "suspected_scam", "other",
]

# 允许的 stage→reason 映射（用于校验，宽松放行，避免过度约束）
STAGE_REASON_MAP = {
    "apply": {"apply_failed", "duplicate", "position_closed", "invalid_position"},
    "viewed": {"viewed_no_reply", "viewed_timeout"},
    "contacted": {"contact_no_reply", "contact_rejected"},
    "replied": {"contact_no_reply", "contact_rejected", "other"},
    "resume_sent": {"resume_rejected", "resume_no_reply"},
    "interview_1": {"interview_1_rejected", "technical_gap", "experience_gap", "salary_mismatch", "culture_mismatch", "other"},
    "interview_2": {"interview_2_rejected", "technical_gap", "experience_gap", "salary_mismatch", "culture_mismatch", "other"},
    "offer": {"offer_rejected", "offer_salary_low", "offer_other", "candidate_withdrew", "company_withdrew", "other"},
    "closed": {"position_closed", "candidate_withdrew", "company_withdrew", "suspected_scam", "other"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS failure_reasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    company TEXT,
    position TEXT,
    resume_version TEXT,
    failure_stage TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, failure_stage, failure_reason)
);
CREATE INDEX IF NOT EXISTS idx_fr_job ON failure_reasons(job_id);
CREATE INDEX IF NOT EXISTS idx_fr_resume ON failure_reasons(resume_version);
CREATE INDEX IF NOT EXISTS idx_fr_stage ON failure_reasons(failure_stage);
"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    return conn


def _validate_stage(stage: str):
    if stage not in FAILURE_STAGES:
        raise ValueError(f"非法 failure_stage: {stage}（合法值: {FAILURE_STAGES}）")


def _validate_reason(reason: str):
    if reason not in FAILURE_REASONS:
        raise ValueError(f"非法 failure_reason: {reason}（合法值: {FAILURE_REASONS}）")


def _resolve_job(job_id: str) -> Optional[dict]:
    """从 applications 表解析 job_id 对应的公司/岗位/简历版本。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT company, job_title, resume_version FROM applications WHERE id=? OR job_title=? OR company=?",
        (job_id, job_id, job_id),
    ).fetchone()
    conn.close()
    if row:
        return {"company": row[0], "position": row[1], "resume_version": row[2]}
    return None


def record_failure(
    job_id: str,
    failure_stage: str,
    failure_reason: str,
    resume_version: Optional[str] = None,
    note: Optional[str] = None,
    source: str = "manual",
) -> dict:
    """记录一条失败原因。幂等：同 job_id+stage+reason 不重复写入（更新 updated_at）。"""
    _validate_stage(failure_stage)
    _validate_reason(failure_reason)

    # 解析 job 上下文（job_id 可能是 id / job_title / company）
    job_ctx = _resolve_job(job_id)
    if job_ctx is None:
        raise ValueError(f"job_id 不存在于 applications 表: {job_id}")

    company = job_ctx["company"]
    position = job_ctx["position"]
    rv = resume_version or job_ctx["resume_version"] or ""

    now = datetime.now().isoformat()
    conn = _get_conn()
    # 幂等：同 job_id+stage+reason 已存在 → 只更新 updated_at/note，不新增
    existing = conn.execute(
        "SELECT id FROM failure_reasons WHERE job_id=? AND failure_stage=? AND failure_reason=?",
        (job_id, failure_stage, failure_reason),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE failure_reasons SET updated_at=?, note=COALESCE(?, note), resume_version=? WHERE id=?",
            (now, note, rv, existing[0]),
        )
        conn.commit()
        conn.close()
        return {"status": "updated", "id": existing[0], "duplicate": True}

    cur = conn.execute(
        """INSERT INTO failure_reasons
           (job_id, company, position, resume_version, failure_stage, failure_reason, source, note, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (job_id, company, position, rv, failure_stage, failure_reason, source, note, now, now),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"status": "created", "id": new_id, "duplicate": False}


def get_failure_reasons(job_id: Optional[str] = None) -> list[dict]:
    conn = _get_conn()
    if job_id:
        rows = conn.execute(
            "SELECT * FROM failure_reasons WHERE job_id=? ORDER BY created_at DESC", (job_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM failure_reasons ORDER BY created_at DESC").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM failure_reasons LIMIT 1").description]
    conn.close()
    return [dict(zip(cols, r)) for r in rows]


def get_failure_stats(
    resume_version: Optional[str] = None,
    failure_stage: Optional[str] = None,
    failure_reason: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """按 failure_reason 分组统计。可按 resume_version/stage/日期范围过滤。"""
    q = "SELECT resume_version, failure_stage, failure_reason, COUNT(*) as cnt FROM failure_reasons WHERE 1=1"
    args = []
    if resume_version:
        q += " AND resume_version=?"
        args.append(resume_version)
    if failure_stage:
        q += " AND failure_stage=?"
        args.append(failure_stage)
    if failure_reason:
        q += " AND failure_reason=?"
        args.append(failure_reason)
    if start_date:
        q += " AND created_at>=?"
        args.append(start_date + "T00:00:00")
    if end_date:
        q += " AND created_at<=?"
        args.append(end_date + "T23:59:59")
    q += " GROUP BY resume_version, failure_stage, failure_reason ORDER BY cnt DESC"
    conn = _get_conn()
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return [{"resume_version": r[0], "failure_stage": r[1], "failure_reason": r[2], "count": r[3]} for r in rows]


def get_failure_funnel(
    resume_version: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """按 failure_stage 分组的漏斗统计（各阶段失败数）。"""
    q = "SELECT failure_stage, COUNT(*) as cnt FROM failure_reasons WHERE 1=1"
    args = []
    if resume_version:
        q += " AND resume_version=?"
        args.append(resume_version)
    if start_date:
        q += " AND created_at>=?"
        args.append(start_date + "T00:00:00")
    if end_date:
        q += " AND created_at<=?"
        args.append(end_date + "T23:59:59")
    q += " GROUP BY failure_stage ORDER BY failure_stage"
    conn = _get_conn()
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return [{"failure_stage": r[0], "count": r[1]} for r in rows]


def get_resume_experiment_summary(start_date: str, end_date: str) -> list[dict]:
    """三线简历实验汇总：A/B/C 各版本的漏斗 + 转化率 + 失败原因 TOP。

    数据源：applications 表（漏斗各阶段数量）+ failure_reasons 表（失败原因）。
    """
    conn = _get_conn()
    results = []
    for rv in ["A-AI应用", "B-解决方案", "C-车联网"]:
        # 应用数（该简历版本投递数）
        applied = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE resume_version=? AND day>=? AND day<=?",
            (rv, start_date, end_date),
        ).fetchone()[0]
        if applied == 0:
            results.append({"resume_version": rv, "applications": 0,
                            "note": "无投递数据"})
            continue
        # 漏斗各阶段（applications 表已有 read/replied/interview/technical_interview/offer 字段）
        viewed = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE resume_version=? AND day>=? AND day<=? AND read=1",
            (rv, start_date, end_date)).fetchone()[0]
        replied = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE resume_version=? AND day>=? AND day<=? AND replied=1",
            (rv, start_date, end_date)).fetchone()[0]
        interview1 = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE resume_version=? AND day>=? AND day<=? AND interview=1",
            (rv, start_date, end_date)).fetchone()[0]
        interview2 = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE resume_version=? AND day>=? AND day<=? AND technical_interview=1",
            (rv, start_date, end_date)).fetchone()[0]
        offer = conn.execute(
            "SELECT COUNT(*) FROM applications WHERE resume_version=? AND day>=? AND day<=? AND offer=1",
            (rv, start_date, end_date)).fetchone()[0]

        # 失败原因 TOP 3
        top_failures = conn.execute(
            """SELECT failure_reason, COUNT(*) as cnt FROM failure_reasons
               WHERE resume_version=? AND created_at>=? AND created_at<=?
               GROUP BY failure_reason ORDER BY cnt DESC LIMIT 3""",
            (rv, start_date + "T00:00:00", end_date + "T23:59:59"),
        ).fetchall()

        results.append({
            "resume_version": rv,
            "applications": applied,
            "viewed": viewed,
            "contacted": 0,  # contacted 无独立字段，用 replied 近似（待 S4 状态机补齐）
            "replied": replied,
            "resume_sent": 0,  # resume_sent 无独立字段（待 S4）
            "interview_1": interview1,
            "interview_2": interview2,
            "offer": offer,
            "view_rate": round(viewed * 100 / applied, 1),
            "reply_rate": round(replied * 100 / applied, 1),
            "interview_rate": round(interview1 * 100 / applied, 1),
            "offer_rate": round(offer * 100 / applied, 1),
            "top_failure_reasons": [{"reason": r[0], "count": r[1]} for r in top_failures],
        })
    conn.close()
    return results


# ── CLI ──
def _cmd_record(args):
    try:
        r = record_failure(args.job_id, args.stage, args.reason, args.resume, args.note)
        print(f"✅ {'更新' if r['duplicate'] else '新增'} 失败原因记录 id={r['id']}"
              f"{'（重复，已更新 updated_at）' if r['duplicate'] else ''}")
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)


def _cmd_stats(args):
    rows = get_failure_stats(
        resume_version=args.resume,
        failure_stage=args.stage,
        failure_reason=args.reason,
        start_date=args.start,
        end_date=args.end,
    )
    if not rows:
        print("（无失败原因记录）")
        return
    print(f"{'简历版本':12s} {'阶段':12s} {'原因':24s} 数量")
    for r in rows:
        print(f"{r['resume_version'] or '-':12s} {r['failure_stage']:12s} {r['failure_reason']:24s} {r['count']}")


def _cmd_funnel(args):
    rows = get_failure_funnel(resume_version=args.resume, start_date=args.start, end_date=args.end)
    if not rows:
        print("（无失败原因记录）")
        return
    print(f"{'阶段':12s} 数量")
    for r in rows:
        print(f"{r['failure_stage']:12s} {r['count']}")


def _cmd_experiment(args):
    start = args.start or "2026-08-14"
    end = args.end or "2026-08-20"
    print(f"三线简历实验汇总（{start} ~ {end}）\n")
    for r in get_resume_experiment_summary(start, end):
        if r.get("note"):
            print(f"{r['resume_version']}: {r['note']}")
            continue
        print(f"【{r['resume_version']}】")
        print(f"  投递 {r['applications']} → 查看 {r['viewed']} → 回复 {r['replied']} → 一面 {r['interview_1']} → 二面 {r['interview_2']} → Offer {r['offer']}")
        print(f"  查看率 {r['view_rate']}% | 回复率 {r['reply_rate']}% | 面试率 {r['interview_rate']}% | Offer率 {r['offer_rate']}%")
        if r['top_failure_reasons']:
            top = ", ".join(f"{x['reason']}({x['count']})" for x in r['top_failure_reasons'])
            print(f"  失败TOP: {top}")
        print()


def main():
    ap = argparse.ArgumentParser(description="S5 失败原因追踪")
    sub = ap.add_subparsers(dest="cmd")

    p_rec = sub.add_parser("record", help="记录失败原因")
    p_rec.add_argument("job_id", help="job_id（applications 的 id/job_title/company）")
    p_rec.add_argument("stage", help=f"失败阶段（{FAILURE_STAGES}）")
    p_rec.add_argument("reason", help=f"失败原因（{FAILURE_REASONS}）")
    p_rec.add_argument("--resume", help="简历版本覆盖（A-AI应用/B-解决方案/C-车联网）")
    p_rec.add_argument("--note", help="备注")

    p_stats = sub.add_parser("stats", help="统计失败原因")
    p_stats.add_argument("--resume", help="按简历版本过滤")
    p_stats.add_argument("--stage", help="按阶段过滤")
    p_stats.add_argument("--reason", help="按原因过滤")
    p_stats.add_argument("--start", help="起始日期 YYYY-MM-DD")
    p_stats.add_argument("--end", help="结束日期 YYYY-MM-DD")

    p_funnel = sub.add_parser("funnel", help="按阶段漏斗")
    p_funnel.add_argument("--resume", help="按简历版本过滤")
    p_funnel.add_argument("--start", help="起始日期")
    p_funnel.add_argument("--end", help="结束日期")

    p_exp = sub.add_parser("experiment", help="三线简历实验汇总")
    p_exp.add_argument("--start", help="起始日期（默认 2026-08-14）")
    p_exp.add_argument("--end", help="结束日期（默认 2026-08-20）")

    args = ap.parse_args()
    if args.cmd == "record":
        _cmd_record(args)
    elif args.cmd == "stats":
        _cmd_stats(args)
    elif args.cmd == "funnel":
        _cmd_funnel(args)
    elif args.cmd == "experiment":
        _cmd_experiment(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

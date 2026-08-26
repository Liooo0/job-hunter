"""Job Hunter 单一事实源（SQLite）

P0 目标：同一投递事实不再同时写在"城市 JSON + ab_experiment.db + failure_reasons"三处。

本模块是唯一的数据写入入口：
- jobs            岗位快照（job_id 稳定，幂等 upsert）
- applications_v2 投递/跳过/失败/不确定 记录（含状态机字段）
- events          事件日志（每个动作一条，带 payload/error/traceback）

旧版 *-log.json 保留为只读历史数据，通过 migrate_legacy_logs() 一次性导入；
迁移完成后所有读取（报告/跟进/熔断/A-B）都走本库。
旧的 applications 表（A/B 漏斗）不再写入，保留为历史视图。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).resolve().parent
DB = PROJECT / "ab_experiment.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'boss',
    city TEXT,
    company TEXT,
    company_norm TEXT,
    title TEXT,
    salary TEXT,
    jd_text TEXT,
    keyword TEXT,
    score INTEGER,
    deep_filter_reason TEXT,
    discovered_at TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_norm);

CREATE TABLE IF NOT EXISTS applications_v2 (
    application_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'boss',
    city TEXT,
    company TEXT,
    company_norm TEXT,
    title TEXT,
    salary TEXT,
    keyword TEXT,
    jd_text TEXT,
    score INTEGER,
    resume_version TEXT,
    decision TEXT NOT NULL,          -- applied / uncertain / skipped / failed
    status TEXT NOT NULL,            -- APPLIED / UNCERTAIN / SKIPPED / FAILED / ACTION_STARTED
    reason TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    read INTEGER NOT NULL DEFAULT 0,
    replied INTEGER NOT NULL DEFAULT 0,
    interview INTEGER NOT NULL DEFAULT 0,
    technical_interview INTEGER NOT NULL DEFAULT 0,
    offer INTEGER NOT NULL DEFAULT 0,
    reject_reason TEXT,
    gates TEXT,                    -- v2.1 决策链快照 JSON（decision_trace.to_json）
    greeting_template_id TEXT,     -- v2.1 招呼语模板版本（如 T2:Python）
    applied_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_v2_job ON applications_v2(job_id);
CREATE INDEX IF NOT EXISTS idx_app_v2_time ON applications_v2(applied_at);
CREATE INDEX IF NOT EXISTS idx_app_v2_decision ON applications_v2(decision);
CREATE INDEX IF NOT EXISTS idx_app_v2_company ON applications_v2(company_norm);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT,
    job_id TEXT,
    type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_app ON events(application_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp);
"""


# ── 公司名规范化（A9，从 boss_apply 移入，供去重与 job_id 共用）──
_NORM_CITY_PREFIXES = (
    "深圳市", "深圳", "广州市", "广州", "东莞市", "东莞", "佛山市", "佛山", "惠州市", "惠州",
    "珠海市", "珠海", "中山市", "中山", "上海市", "上海", "北京市", "北京", "杭州市", "杭州",
    "南京市", "南京", "苏州市", "苏州", "无锡市", "无锡", "武汉市", "武汉", "成都市", "成都",
    "重庆市", "重庆", "天津市", "天津", "厦门市", "厦门", "宁波市", "宁波", "长沙市", "长沙",
    "合肥市", "合肥", "济南市", "济南", "昆明市", "昆明", "福州市", "福州", "南宁市", "南宁",
    "大连市", "大连", "青岛市", "青岛", "西安市", "西安",
)
_NORM_CITY_IN_BRACKETS = (
    "深圳|广州|东莞|佛山|惠州|珠海|中山|上海|北京|杭州|南京|苏州|无锡|"
    "武汉|成都|重庆|天津|厦门|宁波|长沙|合肥|济南|昆明|福州|南宁|大连|青岛|西安"
)
_NORM_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "股份公司", "公司")


def normalize_company(company: str) -> str:
    """保守规范化公司名用于去重匹配，返回小写规范名。"""
    if not company:
        return ""
    s = str(company).strip()
    s = re.sub(r"[（(](?:%s)[^）)]*[）)]" % _NORM_CITY_IN_BRACKETS, "", s)
    for suf in _NORM_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    for city in _NORM_CITY_PREFIXES:
        if s.startswith(city):
            s = s[len(city):]
            break
    return s.strip(" ·-—()（）").lower()


# ── v2.1 增量列迁移（幂等：新库由 SCHEMA 直接带列；旧库 ALTER 补列，重复执行无副作用）──
_V21_EXTRA_COLUMNS = (
    ("applications_v2", "gates"),
    ("applications_v2", "greeting_template_id"),
)
_v21_columns_ready = False


def _ensure_v21_columns(conn):
    """给旧库补 v2.1 新列。已存在则跳过（duplicate column 幂等忽略）。"""
    global _v21_columns_ready
    if _v21_columns_ready:
        return
    for table, col in _V21_EXTRA_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在/并发迁移 → 幂等
    _v21_columns_ready = True


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _ensure_v21_columns(conn)
    return conn


def _job_id(platform: str, city: str, company_norm: str, title: str) -> str:
    raw = f"{platform}|{city}|{company_norm}|{title}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _application_id(platform: str, city: str, company_norm: str, title: str, applied_at: str) -> str:
    raw = f"{platform}|{city}|{company_norm}|{title}|{applied_at}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _event_payload(platform, city, company, title, salary, keyword,
                   score, decision, status, reason, verified, extra_payload=None) -> dict:
    """事件 payload（键序固定，保证与历史记录字节一致；extra_payload 仅追加新键）。"""
    payload = {
        "platform": platform, "city": city, "company": company,
        "title": title, "salary": salary, "keyword": keyword,
        "score": score, "decision": decision, "status": status,
        "reason": reason, "verified": verified,
    }
    if extra_payload:
        payload.update(extra_payload)
    return payload


def record_application(
    *,
    platform: str = "boss",
    city: str = "",
    company: str = "",
    title: str = "",
    salary: str = "",
    keyword: str = "",
    jd_text: str = "",
    score: int = 0,
    resume_version: str = "",
    decision: str,
    status: str,
    reason: str = "",
    verified: int = 0,
    applied_at: Optional[str] = None,
    event_type: Optional[str] = None,
    event_error: Optional[str] = None,
    extra_payload: Optional[dict] = None,
    gates: Optional[str] = None,
    greeting_template_id: Optional[str] = None,
) -> str:
    """写入一条 application 记录 + 一条 event。返回 application_id。

    decision/status 取值约定：
      applied    / APPLIED      已验证成功（会话打开 + 消息发出）
      uncertain  / UNCERTAIN    动作已发生但未完全验证（人工复核）
      skipped    / SKIPPED      规则过滤/去重/已沟通过
      failed     / FAILED       动作失败（弹窗拦截/异常）

    extra_payload：可选，合并进事件 payload JSON（如 A8 的 traceback 字段），
    不传时与旧行为完全一致。
    gates：可选，v2.1 决策链快照 JSON 字符串（decision_trace.to_json 产物）；
    同时并入 events payload。不传时该列落 NULL（旧数据兼容）。
    greeting_template_id：可选，v2.1 招呼语模板版本标识。
    """
    applied_at = applied_at or datetime.now().isoformat()
    now = datetime.now().isoformat()
    cn = normalize_company(company)
    jid = _job_id(platform, city, cn, title)
    aid = _application_id(platform, city, cn, title, applied_at)

    merged_extra = dict(extra_payload) if extra_payload else {}
    if gates:
        merged_extra.setdefault("gates", gates)

    conn = _conn()
    conn.execute(
        """INSERT OR IGNORE INTO jobs
           (job_id, platform, city, company, company_norm, title, salary, jd_text, keyword, score, discovered_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (jid, platform, city, company, cn, title, salary, jd_text, keyword, score, applied_at, now),
    )
    # UPSERT：重复写入只刷新动作字段，保留已补录的漏斗状态（read/replied/interview/offer 等）
    conn.execute(
        """INSERT INTO applications_v2
           (application_id, job_id, platform, city, company, company_norm, title, salary, keyword,
            jd_text, score, resume_version, decision, status, reason, verified,
            gates, greeting_template_id, applied_at, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(application_id) DO UPDATE SET
             decision=excluded.decision, status=excluded.status, reason=excluded.reason,
             verified=excluded.verified, updated_at=excluded.updated_at""",
        (aid, jid, platform, city, company, cn, title, salary, keyword,
         jd_text, score, resume_version, decision, status, reason, verified,
         gates, greeting_template_id, applied_at, now, now),
    )
    conn.execute(
        """INSERT INTO events (application_id, job_id, type, timestamp, payload, error)
           VALUES (?,?,?,?,?,?)""",
        (
            aid, jid, event_type or decision, applied_at,
            json.dumps(_event_payload(platform, city, company, title, salary,
                                      keyword, score, decision, status,
                                      reason, verified, merged_extra or None),
                       ensure_ascii=False),
            event_error,
        ),
    )
    conn.commit()
    conn.close()
    return aid


def record_event(
    application_id: str,
    job_id: str,
    event_type: str,
    payload: Optional[dict] = None,
    error: Optional[str] = None,
):
    conn = _conn()
    conn.execute(
        "INSERT INTO events (application_id, job_id, type, timestamp, payload, error) VALUES (?,?,?,?,?,?)",
        (application_id, job_id, event_type, datetime.now().isoformat(),
         json.dumps(payload or {}, ensure_ascii=False), error),
    )
    conn.commit()
    conn.close()


def count_applied_since(start_iso: str, include_uncertain: bool = True) -> int:
    """统计某时刻之后发生的投递动作数（跨进程熔断用）。

    include_uncertain=True 时把 UNCERTAIN 也计入（会话已打开即视为产生过动作）。
    """
    decisions = "('applied','uncertain')" if include_uncertain else "('applied')"
    conn = _conn()
    n = conn.execute(
        f"SELECT COUNT(*) AS c FROM applications_v2 WHERE decision IN {decisions} AND applied_at >= ?",
        (start_iso,),
    ).fetchone()["c"]
    conn.close()
    return n


def list_city_titles(city: str, platform: str = "boss") -> set[str]:
    conn = _conn()
    rows = conn.execute(
        "SELECT title FROM applications_v2 WHERE city=? AND platform=?", (city, platform)
    ).fetchall()
    conn.close()
    return {r["title"] for r in rows}


def company_applied_recently(city: str, company: str, days: int, platform: str = "boss") -> bool:
    """该公司×该城市 N 天内是否已有投递动作（规范化匹配，防跨关键词重复投）。"""
    cn = normalize_company(company)
    if not cn or days <= 0:
        return False
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = _conn()
    row = conn.execute(
        """SELECT 1 AS x FROM applications_v2
           WHERE platform=? AND city=? AND company_norm=? AND decision IN ('applied','uncertain') AND applied_at >= ?
           LIMIT 1""",
        (platform, city, cn, cutoff),
    ).fetchone()
    conn.close()
    return row is not None


def merge_records() -> dict:
    """返回与旧版 merge_logs 同构的记录（applied/skipped/failed），供报告直接使用。

    applied 数组包含 applied + uncertain（uncertain 带 status/verified 标记，报告中可单独统计）。
    """
    conn = _conn()
    rows = conn.execute("SELECT * FROM applications_v2 ORDER BY applied_at").fetchall()
    conn.close()
    out = {"applied": [], "skipped": [], "failed": []}
    for r in rows:
        entry = {
            "company": r["company"], "job": r["title"], "salary": r["salary"],
            "score": r["score"], "reason": r["reason"], "city": r["city"],
            "keyword": r["keyword"], "resume_version": r["resume_version"],
            "time": r["applied_at"], "status": r["status"], "verified": r["verified"],
        }
        if r["decision"] in ("applied", "uncertain"):
            out["applied"].append(entry)
        elif r["decision"] == "skipped":
            out["skipped"].append(entry)
        elif r["decision"] == "failed":
            out["failed"].append(entry)
    return out


def recent_activity_days(days: int = 7) -> list[dict]:
    """按 entry.time 聚合近 N 天投递量（SQLite 版，与 shared.recent_activity 同构）。"""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = _conn()
    rows = conn.execute(
        "SELECT applied_at, decision FROM applications_v2 WHERE applied_at >= ?",
        (cutoff + "T00:00:00",),
    ).fetchall()
    conn.close()
    daily: dict[str, dict] = {}
    for r in rows:
        day = (r["applied_at"] or "")[:10]
        if not day:
            continue
        d = daily.setdefault(day, {"date": day, "applied": 0, "skipped": 0, "failed": 0})
        if r["decision"] in ("applied", "uncertain"):
            d["applied"] += 1
        elif r["decision"] == "skipped":
            d["skipped"] += 1
        elif r["decision"] == "failed":
            d["failed"] += 1
    return sorted(daily.values(), key=lambda x: x["date"])


def applications(decision_in: Optional[tuple[str, ...]] = None, platform: str = "boss") -> list[dict]:
    """返回 applications_v2 行（dict），供 A/B 漏斗等消费。"""
    conn = _conn()
    if decision_in:
        marks = ",".join("?" * len(decision_in))
        rows = conn.execute(
            f"SELECT * FROM applications_v2 WHERE platform=? AND decision IN ({marks}) ORDER BY applied_at",
            (platform, *decision_in),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM applications_v2 ORDER BY applied_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_empty() -> bool:
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) AS c FROM applications_v2").fetchone()["c"]
    conn.close()
    return n == 0


def migrate_legacy_logs(verbose: bool = True) -> dict:
    """把旧版 *-log.json 幂等导入 applications_v2（以 application_id 去重）。

    旧 JSON 仍保留不删；新写入一律走本库。
    """
    counts = {"applied": 0, "skipped": 0, "failed": 0, "inserted": 0, "files": 0}
    conn = _conn()
    for f in sorted(PROJECT.glob("*-log.json")):
        platform = "51job" if f.name.startswith("51job") else "boss"
        city = (
            f.name.replace("boss-", "").replace("51job-", "")
            .replace("-log.json", "")
        )
        # 缺 time 的旧记录用文件 mtime（当天中午）兜底，避免全部落到"现在"污染熔断计数
        file_day = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%dT12:00:00")
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        counts["files"] += 1
        for key, decision, status in (
            ("applied", "applied", "APPLIED"),
            ("skipped", "skipped", "SKIPPED"),
            ("failed", "failed", "FAILED"),
        ):
            for seq, e in enumerate(d.get(key, [])):
                title = e.get("job") or e.get("title") or ""
                company = e.get("company") or ""
                cn = normalize_company(company)
                raw_time = e.get("time") or ""
                applied_at = raw_time if raw_time else file_day
                # 缺 time 的记录无法用真实时刻，用"文件日 + 文件内序号"生成稳定 ID，
                # 既保留每条历史记录，也不把时间伪造到今天。
                if raw_time:
                    aid = _application_id(platform, city, cn, title, applied_at)
                else:
                    raw = f"{platform}|{city}|{cn}|{title}|{file_day}|{key}|{seq}"
                    aid = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
                jid = _job_id(platform, city, cn, title)
                now = datetime.now().isoformat()
                reason = e.get("reason") or (e.get("error") or "") if key != "applied" else e.get("reason") or ""
                conn.execute(
                    """INSERT OR IGNORE INTO jobs
                       (job_id, platform, city, company, company_norm, title, salary, keyword, score, discovered_at, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (jid, platform, city, company, cn, title, e.get("salary") or "",
                     e.get("keyword") or "", e.get("score") or 0, applied_at, now),
                )
                cur = conn.execute(
                    """INSERT OR IGNORE INTO applications_v2
                       (application_id, job_id, platform, city, company, company_norm, title, salary,
                        keyword, score, resume_version, decision, status, reason, verified, applied_at, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        aid, jid, platform, city, company, cn, title, e.get("salary") or "",
                        e.get("keyword") or "", e.get("score") or 0,
                        e.get("resume_version") or "", decision, status, reason,
                        1 if key == "applied" else 0, applied_at, now, now,
                    ),
                )
                if cur.rowcount:
                    counts[key] += 1
                    counts["inserted"] += 1
                    conn.execute(
                        "INSERT INTO events (application_id, job_id, type, timestamp, payload, error) VALUES (?,?,?,?,?,?)",
                        (aid, jid, "migrated", applied_at,
                         json.dumps({"source": str(f), "legacy_key": key,
                                     "legacy_time_missing": not raw_time}, ensure_ascii=False), None),
                    )
    conn.commit()
    conn.close()
    if verbose:
        print(f"✅ 旧日志迁移完成: {counts['inserted']} 条新导入"
              f"（applied={counts['applied']} skipped={counts['skipped']} failed={counts['failed']}，"
              f"共 {counts['files']} 个文件）")
    return counts


def ensure_migrated(verbose: bool = False):
    """库为空且有旧 JSON 时自动迁移一次；之后所有读写都走 SQLite。"""
    if is_empty():
        legacy = list(PROJECT.glob("*-log.json"))
        if legacy:
            migrate_legacy_logs(verbose=verbose)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Job Hunter 数据存储工具")
    ap.add_argument("--migrate", action="store_true", help="把旧 *-log.json 导入 SQLite（幂等）")
    ap.add_argument("--counts", action="store_true", help="查看当前各 decision 数量")
    args = ap.parse_args()
    if args.migrate:
        migrate_legacy_logs()
    if args.counts:
        conn = _conn()
        rows = conn.execute("SELECT decision, status, COUNT(*) AS c FROM applications_v2 GROUP BY decision, status ORDER BY decision").fetchall()
        for r in rows:
            print(f"  {r['decision']:10s} / {r['status']:10s} = {r['c']}")
        conn.close()

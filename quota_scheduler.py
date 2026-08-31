#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JOB-HUNTER v5 额度调度器（2026-08-31）— 每日投递预算的确定性记账。

定位：额度 = 预算，不是 KPI。
  - 预算上限 150（candidate_profile.yaml quota.daily_quota），
    但永远被 config.json safety 硬顶夹住：min(quota, normal_daily_cap)。
    为凑额度突破风控/硬规则 = 违反 v5 验收标准 6，代码层面不可能。
  - 只扣真投递：VERIFIED/SENT 才消耗额度；UNCERTAIN 挂"待验证"，
    验证通过才补扣，撤回则释放（uncertain_counts_as_sent: false）。
  - Plan 路由优先级：P1-A > P1-B > P1-C > P1-D > P2-A > P2-B > P2-C。
    额度不足时按排队位次裁剪，永远先保 plan1（验收标准 3）。

记账落 DB：ab_experiment.db 表 quota_ledger（幂等，可断电续算）。
"""
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ab_experiment.db")
PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "candidate_profile.yaml")

# Plan 排队权重：越小越先吃额度（plan1 永远压过 plan2）
PLAN_ORDER = {
    "P1-A": 1, "P1-B": 2, "P1-C": 3, "P1-D": 4,
    "P2-A": 5, "P2-B": 6, "P2-C": 7,
    "": 8,           # 未标 plan 的按最后补位
}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _load_limits() -> tuple:
    """(quota_budget, safety_cap) — 生效上限 = min(两者)。"""
    quota = 150
    try:
        import yaml
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            p = yaml.safe_load(f)
        quota = int(p.get("quota", {}).get("daily_quota", 150))
    except Exception:
        pass
    cap = 50
    try:
        cfg_path = os.path.join(os.path.dirname(PROFILE_PATH), "..", "config.json")
        with open(os.path.abspath(cfg_path), "r", encoding="utf-8") as f:
            cap = int(json.load(f).get("safety", {}).get("normal_daily_cap", 50))
    except Exception:
        pass
    return quota, cap


def _ensure_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS quota_ledger (
        day TEXT NOT NULL,
        job_key TEXT NOT NULL,
        plan TEXT DEFAULT '',
        status TEXT NOT NULL,          -- SENT / UNCERTAIN / VERIFIED / RELEASED
        ts REAL NOT NULL,
        PRIMARY KEY (day, job_key)
    )""")
    return con


@dataclass
class QuotaState:
    day: str
    budget: int          # 生效上限 = min(profile quota, safety cap)
    sent: int            # SENT + VERIFIED（已扣额）
    pending: int         # UNCERTAIN（挂起未扣）
    scanned: int = 0     # 统计口径：发现≠投递，扫描数单独记，不扣额

    @property
    def remaining(self) -> int:
        # UNCERTAIN 虽不算"完成投递"（sent），但占用预留额度：
        # 发送结果未知时若放开预算，可能实际超出风控上限。
        # 验证通过 → 转正扣额；确认失败 → release 回血。
        return max(0, self.budget - self.sent - self.pending)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0


class QuotaScheduler:
    """每日额度账本。boss_apply 在 apply 前 acquire()、结果回来后 confirm()/release()。"""

    def __init__(self, db_path: Optional[str] = None):
        self.con = _ensure_db()
        if db_path:
            self.con = sqlite3.connect(db_path)
            self.con.execute("""CREATE TABLE IF NOT EXISTS quota_ledger (
                day TEXT NOT NULL, job_key TEXT NOT NULL, plan TEXT DEFAULT '',
                status TEXT NOT NULL, ts REAL NOT NULL,
                PRIMARY KEY (day, job_key))""")

    # ── 状态 ──
    def state(self) -> QuotaState:
        day = _today()
        self._reconcile(day)
        q, cap = _load_limits()
        rows = self.con.execute(
            "SELECT status, COUNT(*) FROM quota_ledger WHERE day=? GROUP BY status", (day,)).fetchall()
        c = dict(rows)
        sent = c.get("SENT", 0) + c.get("VERIFIED", 0)
        pending = c.get("UNCERTAIN", 0)
        return QuotaState(day=day, budget=min(q, cap), sent=sent, pending=pending)

    # ── 获取额度（发送前）──
    def acquire(self, job_key: str, plan: str = "") -> bool:
        """True=额度到手可发送。幂等：重复 job_key 不重复扣。"""
        st = self.state()
        if job_key and self._get(job_key) is not None:
            return False   # 今日已处理过（去重）
        if st.exhausted:
            return False
        if job_key:
            self.con.execute(
                "INSERT OR REPLACE INTO quota_ledger(day,job_key,plan,status,ts) VALUES(?,?,?,?,?)",
                (st.day, job_key, plan, "UNCERTAIN", time.time()))
            self.con.commit()
        return True

    # ── 回写结果 ──
    def confirm(self, job_key: str, verified: bool = True):
        """发送结果回来：明确成功→SENT；UNCERTAIN→保持挂起等验证。"""
        self._set(job_key, "SENT" if verified else "UNCERTAIN")

    def verify(self, job_key: str):
        """UNCERTAIN 事后验证送达 → 升级 SENT（此时才真正扣额）。"""
        self._set(job_key, "SENT")

    def release(self, job_key: str):
        """确认未发出 → 释放额度。"""
        self._set(job_key, "RELEASED")

    # ── Plan 裁剪（验收标准 3/4）──
    @staticmethod
    def rank(candidates: List[dict]) -> List[dict]:
        """candidates: [{plan_priority:'P1-A', value_score:92, ...}]。
        先按 plan 档位，同档内按价值分降序。plan 只决定序，不剔除。"""
        return sorted(candidates, key=lambda c: (
            PLAN_ORDER.get(c.get("plan_priority", ""), 9),
            -float(c.get("value_score", 0) or 0)))

    def fit(self, candidates: List[dict]) -> List[dict]:
        """按剩余额度裁剪排序后的候选：能装多少装多少，装不下即止。"""
        remaining = self.state().remaining
        return self.rank(candidates)[:remaining]

    # ── 对账（诚实记账）：quota_ledger 只是预留账，真实扣额以 applications_v2 为准。
    #    每次 state() 前把今日 verified=1 的真实投递幂等补进账本；
    #    已存在（SENT/UNCERTAIN/VERIFIED）的不覆盖。防止接线前的投递漏记，
    #    也防止任何路径绕过账本虚增剩余额度。──
    def _reconcile(self, day: str):
        try:
            rows = self.con.execute(
                """SELECT city, company, title, status FROM applications_v2
                   WHERE date(applied_at)=? AND status IN ('APPLIED','UNCERTAIN')""",
                (day,)).fetchall()
        except sqlite3.OperationalError:
            return   # 同库才有该表；注入独立测试库时跳过
        for city, company, title, status in rows:
            key = f"{city}|{company}|{title}"
            cur = self._get(key)
            if cur is None:
                st = "SENT" if status == "APPLIED" else "UNCERTAIN"
                self.con.execute(
                    "INSERT OR IGNORE INTO quota_ledger(day,job_key,plan,status,ts) VALUES(?,?,?,?,?)",
                    (day, key, "", st, time.time()))
        self.con.commit()

    # ── 内部 ──
    def _get(self, job_key: str):
        r = self.con.execute("SELECT status FROM quota_ledger WHERE day=? AND job_key=?",
                             (_today(), job_key)).fetchone()
        return r[0] if r else None

    def _set(self, job_key: str, status: str):
        self.con.execute("UPDATE quota_ledger SET status=?, ts=? WHERE day=? AND job_key=?",
                         (status, time.time(), _today(), job_key))
        self.con.commit()


if __name__ == "__main__":
    s = QuotaScheduler().state()
    print(f"今日 {s.day} | 生效预算 {s.budget} (profile∩safety) | 已扣 {s.sent} | 待验证 {s.pending} | 剩余 {s.remaining}")

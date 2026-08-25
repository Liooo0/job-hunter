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
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import store
from shared import load_config

PROJECT = Path(__file__).resolve().parent
DB = PROJECT / "ab_experiment.db"

# 实验窗口：因账号 8/12-8/13 封禁，原 8/11~8/16 顺延。
# 解封日 8/14 起重新起算 7 天正式实验。
BASELINE_DAY = date(2026, 8, 10)   # 预实验（不参与裁决）
EXPERIMENT_START = date(2026, 8, 14)
EXPERIMENT_END = date(2026, 8, 20)

# ── 词表加载（P2-T5 / A4：修复实验词表与实际投递词表漂移）──────────
# 唯一事实源 = config.json 的 job_pools.keywords / city_pools.city_priority；
# 下方两个 LEGACY_* 旧硬编码只在 config 缺字段时兜底，不再新增词条。
#
# 历史 category 兼容映射（勿删）：
#   show_funnel 每次都会用 pool_of() 对存量记录的搜索词重新归类（_v2_row_to_legacy），
#   而 applications 表历史行的 category 用的是旧池名。所以 pool_of 的查找顺序：
#     ① LEGACY_POOL_KEYWORDS —— 老关键词继续落回原桶名
#        （如 "AI应用工程师"→"AI应用/AI工程师"，而不是漂移成 "S级-AI应用工程师"）；
#     ② POOL_KEYWORDS（config 新池）—— 只承接旧表没有的新词（如 "车载测试"）。
#   新旧桶在漏斗里各自显示、不互相改名，避免历史实验结论失真。
#   新旧池名对应关系参考（人工维护，仅作文档）：
#     AI应用/AI工程师          ≈ S级-AI应用工程师
#     AI实施/工作流/RAG/Dify    ≈ S级-AI实施/解决方案
#     技术支持/AI产品           ≈ A级-IT技术支持/数字化
#     RPA/Python自动化         ≈ （已并入上面各池，无独立新池）
LEGACY_POOL_KEYWORDS = {
    "AI应用/AI工程师": ["AI应用工程师", "AI工程师", "LLM应用", "大模型应用", "Agent应用", "智能体开发"],
    "AI实施/工作流/RAG/Dify": ["AI实施", "AI实施工程师", "工作流工程师", "RAG", "知识库运营", "Dify", "Coze", "AI解决方案"],
    "RPA/Python自动化": ["RPA开发", "Python开发", "自动化", "数字员工", "低代码开发", "AI自动化"],
    "技术支持/AI产品": ["AI技术支持", "AI产品", "AI运营", "AI训练师", "AI文档"],
}

LEGACY_CITY_PRIORITY = {
    "深圳": "primary",
    "广州": "secondary", "东莞": "secondary", "佛山": "secondary",
    "北京": "opportunistic", "上海": "opportunistic", "杭州": "opportunistic", "成都": "opportunistic",
}


def _load_wordlists():
    """从 config.json 读 job_pools/city_pools；缺字段时回退旧硬编码。

    返回 (POOL_KEYWORDS, CITY_PRIORITY, 来源标记)。"""
    cfg = load_config()
    cfg_pools = (cfg.get("job_pools") or {}).get("keywords") or {}
    cfg_cities = (cfg.get("city_pools") or {}).get("city_priority") or {}
    pools = cfg_pools if cfg_pools else dict(LEGACY_POOL_KEYWORDS)
    cities = cfg_cities if cfg_cities else dict(LEGACY_CITY_PRIORITY)
    source = "config.json" if (cfg_pools and cfg_cities) else "内置兜底(LEGACY)"
    return pools, cities, source


POOL_KEYWORDS, CITY_PRIORITY, WORDLIST_SOURCE = _load_wordlists()

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
    """搜索词 → 岗位池名。先查 LEGACY 旧池（历史桶名兼容，见文件头注释），再查 config 新池，都没有归「其他」。"""
    for pools in (LEGACY_POOL_KEYWORDS, POOL_KEYWORDS):
        for pool, kws in pools.items():
            if keyword in kws:
                return pool
    return "其他"


def import_logs():
    """旧 JSON → SQLite 单一事实源（幂等）。"""
    counts = store.migrate_legacy_logs(verbose=False)
    print(f"✅ 旧日志导入完成: 新增 {counts['inserted']} 条"
          f"（applied={counts['applied']} skipped={counts['skipped']} failed={counts['failed']}）")


def _v2_row_to_legacy(r: dict):
    """把 applications_v2 行映射成旧漏斗行（show_funnel 展示用）。"""
    day = (r.get("applied_at") or "")[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    return {
        "day": day,
        "company": r.get("company", ""),
        "job_title": r.get("title", ""),
        "city": r.get("city", ""),
        "salary": r.get("salary", ""),
        "category": pool_of(r.get("keyword", "")),
        "city_priority": CITY_PRIORITY.get(r.get("city", ""), "other"),
        "jd_score": r.get("score", 0),
        "resume_version": r.get("resume_version", ""),
        "is_baseline": 1 if day == BASELINE_DAY.isoformat() else 0,
        "read": r.get("read", 0),
        "replied": r.get("replied", 0),
        "interview": r.get("interview", 0),
        "technical_interview": r.get("technical_interview", 0),
        "offer": r.get("offer", 0),
        "reject_reason": r.get("reject_reason", ""),
    }


def update_status(company: str, job: str, field: str, value: int = 1):
    valid = {"read", "replied", "interview", "technical_interview", "offer"}
    if field not in valid:
        print(f"❌ 字段必须是 {valid}")
        return
    conn = get_db()
    # 单一事实源（v2）
    cur = conn.execute(
        f"UPDATE applications_v2 SET {field}=? WHERE company LIKE ? AND title LIKE ?",
        (value, f"%{company}%", f"%{job}%"),
    )
    print(f"✅ 更新 applications_v2 {cur.rowcount} 条: {company} {job} → {field}={value}")
    # 旧表同步（历史视图）
    cur = conn.execute(
        f"UPDATE applications SET {field}=? WHERE company LIKE ? AND job_title LIKE ?",
        (value, f"%{company}%", f"%{job}%"),
    )
    conn.commit()
    if cur.rowcount:
        print(f"   旧表同步 {cur.rowcount} 条")
    conn.close()


def set_reject(company: str, job: str, reason: str):
    if reason not in REJECT_REASONS:
        print(f"❌ 原因必须是: {REJECT_REASONS}")
        return
    conn = get_db()
    cur = conn.execute(
        "UPDATE applications_v2 SET reject_reason=? WHERE company LIKE ? AND title LIKE ?",
        (reason, f"%{company}%", f"%{job}%"),
    )
    print(f"✅ 更新 applications_v2 {cur.rowcount} 条: {company} {job} → {reason}")
    cur = conn.execute(
        "UPDATE applications SET reject_reason=? WHERE company LIKE ? AND job_title LIKE ?",
        (reason, f"%{company}%", f"%{job}%"),
    )
    conn.commit()
    if cur.rowcount:
        print(f"   旧表同步 {cur.rowcount} 条")
    conn.close()


def show_funnel():
    print("=" * 70)
    print("7天 A/B 实验漏斗（正式实验 08-14 ~ 08-20，Baseline=08-10 不参与裁决）")
    print("=" * 70)

    # P0：读 applications_v2（单一事实源）；旧 JSON 首次自动迁移
    store.ensure_migrated()
    rows = [_v2_row_to_legacy(r) for r in store.applications(decision_in=("applied", "uncertain"))]
    rows = [r for r in rows if r is not None]

    # 按天（只显示 08-10 起，历史数据不展示）
    by_day = defaultdict(list)
    for r in rows:
        try:
            d_ = date.fromisoformat(r["day"])
        except ValueError:
            continue
        if d_ < BASELINE_DAY:
            continue
        by_day[r["day"]].append(r)

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
    exp = [r for r in rows if r["day"] and EXPERIMENT_START <= date.fromisoformat(r["day"]) <= EXPERIMENT_END]
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
        # 简历版本分布（三线对比核心）
        by_resume = defaultdict(int)
        for r in exp:
            by_resume[r.get("resume_version") or "未标注"] += 1
        print(f"   简历版本: {dict(by_resume)}")
        # 各版本漏斗（已读/回复/面试 按简历版本拆分）
        if any(by_resume.values()):
            print("   分版本漏斗（版本: 已读/回复/面试）:")
            rv_stats = defaultdict(lambda: [0, 0, 0])
            for r in exp:
                v = r.get("resume_version") or "未标注"
                rv_stats[v][0] += 1 if r["read"] else 0
                rv_stats[v][1] += 1 if r["replied"] else 0
                rv_stats[v][2] += 1 if r["interview"] else 0
            for v, s in rv_stats.items():
                print(f"     {v}: {s[0]}/{s[1]}/{s[2]}")


def main():
    ap = argparse.ArgumentParser(description="7天 A/B 实验漏斗")
    ap.add_argument("--import-logs", action="store_true", help="从 boss 日志导入投递")
    ap.add_argument("--update", nargs=3, metavar=("公司", "岗位", "字段"), help="补录状态 read/replied/interview/offer")
    ap.add_argument("--reject", nargs=3, metavar=("公司", "岗位", "原因"), help="记录拒绝原因")
    args = ap.parse_args()

    total_kw = sum(len(v) for v in POOL_KEYWORDS.values())
    print(f"[词表] 来源={WORDLIST_SOURCE} | 分组 {len(POOL_KEYWORDS)} 个 / 关键词 {total_kw} 个 | 城市 {len(CITY_PRIORITY)} 个")

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

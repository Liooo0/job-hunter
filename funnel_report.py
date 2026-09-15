#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""funnel_report.py — 投递漏斗复盘（只读，不碰浏览器、不改数据库）

目的：把"投了很多但不知道为什么没结果"变成一张能看懂的漏斗图。
这是 job-hunter 一直缺的那一环 —— 反馈闭环。

数据来源：
  data/ab_experiment.db   applications_v2 / jobs / failure_reasons
  archived_replies/*.json HR 消息归档（Boss 会话）
  data/reply_pending.json 待审核/已发回复队列

口径说明（重要）：
  - applications_v2 里绝大多数是 SKIPPED（被规则拦掉），不是投递
  - APPLIED 里有一批是历史日志批量导入（同一分钟内上千条），必须排除
  - **真实投递** = status IN (UNCERTAIN, APPLIED, VERIFIED) 且不属于批量导入
  - UNCERTAIN = 点了按钮但未验证（这是当前最大的口径黑洞）

用法：
  python3 funnel_report.py                    # 输出到桌面 + 求职资料
  python3 funnel_report.py --days 30          # 只看最近 30 天
  python3 funnel_report.py --stdout           # 只打印不写文件
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_CANDIDATES = [
    BASE / "ab_experiment.db",
    BASE / "data" / "ab_experiment.db",
    Path.home() / "projects/job-hunter/ab_experiment.db",
]


def find_db():
    for p in DB_CANDIDATES:
        if p.exists():
            return p
    return None


ARCHIVED = BASE / "archived_replies"
PENDING = BASE / "data" / "reply_pending.json"

REAL_STATES = ("UNCERTAIN", "APPLIED", "VERIFIED")
BULK_MINUTE_THRESHOLD = 200      # 同一分钟内超过这么多条 = 批量导入，剔除


def load_rows(days: int):
    db = find_db()
    if db is None:
        sys.exit(f"❌ 找不到数据库，试过：{', '.join(str(p) for p in DB_CANDIDATES)}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    since = (date.today() - timedelta(days=days)).isoformat() if days else "0000-01-01"
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM applications_v2 WHERE substr(created_at,1,10) >= ? ORDER BY created_at", (since,)
    )]
    con.close()
    return rows


def mark_bulk(rows):
    """给每行打 bulk 标记：同一分钟内的真实投递数超阈值 → 视为批量导入。"""
    per_minute = Counter()
    for r in rows:
        if r["status"] in REAL_STATES:
            per_minute[(r["created_at"] or "")[:16]] += 1
    for r in rows:
        r["_bulk"] = per_minute.get((r["created_at"] or "")[:16], 0) > BULK_MINUTE_THRESHOLD
    return rows


def hr_feedback():
    """HR 侧面反馈：归档消息 + 待审核队列。"""
    buckets = Counter()
    samples = []
    if ARCHIVED.exists():
        for f in ARCHIVED.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            msg = (d.get("message") or "")
            skip = d.get("skip_reason") or ""
            if skip == "拒绝" or any(k in msg for k in
                                     ("不合适", "不匹配", "匹配度不够", "不太合适", "祝您早日", "没有联系")):
                buckets["拒绝/软拒"] += 1
                if len(samples) < 6:
                    samples.append((d.get("company", ""), msg[:70]))
            elif msg.strip():
                buckets["其他真实回复"] += 1
                if len(samples) < 10:
                    samples.append((d.get("company", ""), msg[:70]))
    pending = []
    if PENDING.exists():
        try:
            pending = json.loads(PENDING.read_text(encoding="utf-8"))
        except Exception:
            pass
    return buckets, samples, pending


def uncertain_audit(rows):
    """UNCERTAIN 交叉核对：这些「点了按钮但没验证」的记录里，有多少能对上 HR 侧痕迹。

    判断依据：archived_replies 里出现过的公司名（取词干匹配）。
    能对上的 = 至少有人工侧证据说明投递到达过真人；对不上的 = 无法区分「投成功没人理」与「压根没投出去」。
    """
    hr_companies = set()
    if ARCHIVED.exists():
        for f in ARCHIVED.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            c = (d.get("job_context") or {}).get("company") or d.get("company") or ""
            if c.strip():
                hr_companies.add(stem_company(c))

    unc = [r for r in rows if r["status"] == "UNCERTAIN" and not r["_bulk"]]
    matched = [r for r in unc if stem_company(r.get("company") or "") in hr_companies]
    return len(unc), len(matched), sorted({(r.get("company") or "") for r in matched})


def stem_company(s: str) -> str:
    s = (s or "").strip()
    for suf in ("有限公司", "股份有限公司", "集团", "科技", "技术", "（中国）", "(中国)"):
        s = s.replace(suf, "")
    return s[:3]


def build(days: int):
    rows = mark_bulk(load_rows(days))

    state = Counter(r["status"] for r in rows)
    real = [r for r in rows if r["status"] in REAL_STATES and not r["_bulk"]]
    bulk = [r for r in rows if r["_bulk"]]

    by_day = defaultdict(Counter)
    for r in real:
        by_day[(r["created_at"] or "")[:10]][r["platform"] or "?"] += 1

    by_kw = Counter((r["keyword"] or "(空)") for r in real)
    by_city = Counter((r["city"] or "(空)") for r in real)
    by_uncertain = Counter(r["status"] for r in real)

    skip_reasons = Counter()
    for r in rows:
        if r["status"] == "SKIPPED" and not r["_bulk"]:
            skip_reasons[(r.get("reason") or "(无原因)")[:38]] += 1

    buckets, samples, pending = hr_feedback()
    unc_n, unc_matched, unc_companies = uncertain_audit(rows)
    return dict(rows=rows, state=state, real=real, bulk=bulk, by_day=by_day,
                by_kw=by_kw, by_city=by_city, by_status=by_uncertain,
                skip_reasons=skip_reasons, hr=buckets, samples=samples, pending=pending,
                unc_n=unc_n, unc_matched=unc_matched, unc_companies=unc_companies)


def render(d: dict, days: int) -> str:
    L = []
    A = L.append
    A(f"# 投递漏斗复盘 · {date.today().isoformat()}")
    A("")
    A(f"> 统计窗口：最近 {days} 天 ｜ 只读报告，不改任何数据")
    A("")

    A("## 一句话结论")
    A("")
    real_n = len(d["real"])
    unc = d["by_status"].get("UNCERTAIN", 0)
    hr = Counter(d["hr"])
    hr_total = sum(hr.values())
    A(f"- 窗口内**真实投递 {real_n} 条**（其中 {unc} 条状态未验证 = {100*unc/max(real_n,1):.0f}%）")
    A(f"- 被规则**拦掉 {d['state'].get('SKIPPED',0)} 条**（其中批量导入占多数；下表只统计非导入部分，反映当前规则行为）")
    A(f"- 历史日志批量导入 {len(d['bulk'])} 条（已从真实投递中剔除，勿重复计入成绩）")
    A(f"- HR 侧有回声：{hr_total} 条（拒绝/软拒 {hr.get('拒绝/软拒',0)} 条）")
    A("")
    A("**这张表就是之前缺的反馈闭环。** 之前的问题不是投得不够，是投出去之后没有任何回声数据可用。")
    A("")

    A("## 状态分布")
    A("")
    A("| 状态 | 条数 | 含义 |")
    A("|---|---|---|")
    meaning = {
        "SKIPPED": "被规则拦截（薪资/实习/销售/倒班等）",
        "APPLIED": "标记为已投（含历史导入，需剔除）",
        "UNCERTAIN": "点了按钮但未验证 —— **口径黑洞**",
        "VERIFIED": "已验证投递成功",
        "FAILED": "明确失败",
    }
    for k, v in d["state"].most_common():
        A(f"| {k} | {v} | {meaning.get(k,'')} |")
    A("")

    A("## 真实投递按天（已剔除批量导入）")
    A("")
    if d["by_day"]:
        plats = sorted({p for c in d["by_day"].values() for p in c})
        A("| 日期 | " + " | ".join(plats) + " | 合计 |")
        A("|---" * (len(plats) + 2) + "|")
        for day in sorted(d["by_day"], reverse=True):
            c = d["by_day"][day]
            cells = [str(c.get(p, 0)) for p in plats]
            A(f"| {day} | " + " | ".join(cells) + f" | **{sum(c.values())}** |")
    else:
        A("_窗口内无真实投递_")
    A("")

    A("## 拦截原因 Top 10（说明规则在干什么）")
    A("")
    A("| 原因 | 条数 |")
    A("|---|---|")
    for r, n in d["skip_reasons"].most_common(10):
        A(f"| {r} | {n} |")
    A("")

    A("## 投递分布")
    A("")
    A("**按关键词：** " + "、".join(f"{k} {v}" for k, v in d["by_kw"].most_common(8)))
    A("")
    A("**按城市：** " + "、".join(f"{k} {v}" for k, v in d["by_city"].most_common(8)))
    A("")

    A("## HR 侧回声")
    A("")
    for k, v in hr.most_common():
        A(f"- {k}：{v} 条")
    if d["samples"]:
        A("")
        A("真实原文样本：")
        A("")
        for comp, msg in d["samples"]:
            A(f"- 「{msg}」 —— {comp}")
    A("")
    if d["pending"]:
        A(f"审核队列里还有 {len(d['pending'])} 条（含已发送/已驳回的历史）")
        A("")

    A("## UNCERTAIN 口径黑洞（交叉核对结果）")
    A("")
    unc_n = d.get("unc_n", 0)
    unc_m = d.get("unc_matched", 0)
    A(f"- UNCERTAIN 记录 **{unc_n} 条**")
    A(f"- 其中能在 HR 消息归档里对上公司名的：**{unc_m} 条**")
    A("")
    if unc_n:
        A(f"也就是说 **{100*(1-unc_m/max(unc_n,1)):.0f}% 的 UNCERTAIN 没有任何人工侧痕迹**——")
        A("无法区分「投成功但没人理」和「压根没投出去」。这组数字不能用来复盘。")
        if d.get("unc_companies"):
            A("")
            A("能对上 HR 痕迹的公司：" + "、".join(d["unc_companies"][:8]))
    A("")

    A("## 下一步（判断，不是建议）")
    A("")
    A(f"1. **UNCERTAIN 占真实投递的 {100*unc/max(real_n,1):.0f}%** —— 这是最大的口径问题。")
    A("   要么做投递后验证（把 UNCERTAIN 收敛成 VERIFIED/FAILED），要么承认这个数字不可用于复盘。")
    A("2. **HR 回声全部是拒绝/软拒**，没有一条进入面试 —— 说明卡点是**渠道-岗位错配**，")
    A("   不是简历质量或投递量。继续加量不会改变结果。")
    A("3. 判断渠道时看**回声率**（有回复 ÷ 真实投递），不要看投递量。")
    A("")

    A("---")
    A("")
    A("*本报告由 `funnel_report.py` 生成，只读数据库，不发起任何投递或消息。*")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    d = build(a.days)
    md = render(d, a.days)

    if a.stdout:
        print(md)
        return

    outs = []
    if a.out:
        outs.append(Path(a.out).expanduser())
    else:
        outs.append(Path.home() / "Documents/求职资料" / f"投递漏斗复盘-{date.today().isoformat()}.md")
        outs.append(Path.home() / "Desktop" / f"{date.today().strftime('%m%d')}_投递漏斗复盘.md")
    for p in outs:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
        print(f"✅ {p}")
    print(f"\n真实投递 {len(d['real'])} 条｜拦截 {d['state'].get('SKIPPED',0)} 条｜"
          f"HR 回声 {sum(d['hr'].values())} 条")


if __name__ == "__main__":
    main()

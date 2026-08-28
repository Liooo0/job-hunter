#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五维评分引擎套用到全部扫描记录 + 统计报表。只读events，结果写match_scores(幂等)。
用法: /usr/bin/python3 jobscore_stats.py [--days N] [--all]"""
import argparse, json, sqlite3, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, "/Users/liuwendi/projects/job-hunter")
from match_engine import explain_match

DB = "/Users/liuwendi/projects/job-hunter/ab_experiment.db"
SCORE_TYPES = {"smart_filter", "deep_filter", "uncertain", "below_min_score", "applied", "decision_trace", "already_chatted", "chat_not_opened"}
FAM = ["采购", "供应链", "寻源", "跟单", "供应商", "SQE", "商务", "项目", "硬件", "测试", "实施", "交付", "自动化", "运维", "售前", "技术支持"]


def job_family(title):
    for f in FAM:
        if f in title:
            return f
    return "其他"


def salary_low_k(sal):
    import re
    if not sal:
        return 0
    s = re.sub(r"[\ue030-\ue039]", "", sal)
    m = re.search(r"(\d+)", s)
    if not m:
        return 0
    v = int(m.group(1))
    return v / 1000 if "元" in sal and v > 100 else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS match_scores(
        id INTEGER PRIMARY KEY,
        job_key TEXT UNIQUE, platform TEXT, city TEXT, company TEXT, title TEXT,
        salary TEXT, keyword TEXT, score_low REAL, fam TEXT,
        total INTEGER, verdict TEXT, t_score INTEGER, d_score INTEGER,
        e_score INTEGER, c_score INTEGER, l_score INTEGER,
        top_risk TEXT, top_gap TEXT, scored_at TEXT)""")

    where = "" if args.all else "AND timestamp >= ?"
    params = [] if args.all else [(datetime.now() - timedelta(days=args.days)).isoformat()]
    rows = con.execute(
        f"SELECT payload FROM events WHERE type IN ({','.join('?'*len(SCORE_TYPES))}) {where}",
        list(SCORE_TYPES) + params).fetchall()

    seen = {}
    for (p,) in rows:
        try:
            d = json.loads(p)
        except Exception:
            continue
        t, c = d.get("title", ""), d.get("company", "")
        if not t or not c:
            continue
        key = f"{c}|{t}|{d.get('city','')}"
        if key not in seen:
            seen[key] = d

    now = datetime.now().isoformat(timespec="seconds")
    scored = 0
    for key, d in seen.items():
        r = explain_match(d.get("title", ""), "", company=d.get("company", ""),
                          salary=d.get("salary", ""), city=d.get("city", "深圳"))
        dims = r.get("dimensions", {})
        gaps = r.get("gaps", []) or []
        risks = r.get("hard_risks", []) or []
        con.execute("""INSERT OR REPLACE INTO match_scores
            (job_key,platform,city,company,title,salary,keyword,score_low,fam,total,verdict,
             t_score,d_score,e_score,c_score,l_score,top_risk,top_gap,scored_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, d.get("platform", "boss"), d.get("city", ""), d.get("company", ""),
             d.get("title", ""), d.get("salary", ""), d.get("keyword", ""),
             salary_low_k(d.get("salary", "")), job_family(d.get("title", "")),
             r["total"], r["verdict"],
             dims.get("technical", {}).get("score", 0), dims.get("direction", {}).get("score", 0),
             dims.get("experience", {}).get("score", 0), dims.get("culture", {}).get("score", 0),
             dims.get("location", {}).get("score", 0),
             risks[0] if risks else "", gaps[0] if gaps else "", now))  # 19 values
        scored += 1
    con.commit()
    return con, scored, seen


def report(con):
    q = lambda s, *a: con.execute(s, a).fetchall()
    total = q("select count(*) from match_scores")[0][0]
    print(f"\n{'='*58}\n五维评分覆盖: {total} 个不同岗位\n{'='*58}")

    print("\n【1】总体判定分布")
    for v, n, avg in q("select verdict,count(*),round(avg(total),1) from match_scores group by verdict order by count(*) desc"):
        print(f"  {v or '?':16s} {n:4d}  均分{avg}")

    print("\n【2】按岗位族（五维均分）")
    print(f"  {'族':8s} {'数':>4s} {'总分':>5s} {'技术':>4s} {'方向':>4s} {'经验':>4s} {'文化':>4s} {'地点':>4s}  强推数")
    for fam, n, t, tc, dc, ec, cc, lc, strong in q(
            "select fam,count(*),round(avg(total),1),round(avg(t_score),1),round(avg(d_score),1),"
            "round(avg(e_score),1),round(avg(c_score),1),round(avg(l_score),1),"
            "sum(case when verdict in ('strong_apply','apply') then 1 else 0 end) "
            "from match_scores group by fam having count(*)>=3 order by avg(total) desc"):
        print(f"  {fam:8s} {n:4d} {t:5.1f} {tc:4.1f} {dc:4.1f} {ec:4.1f} {cc:4.1f} {lc:4.1f}  {strong}")

    print("\n【3】城市×高分（≥60可投线以上）")
    for city, n, hi in q("select city,count(*),sum(case when total>=60 then 1 else 0 end) as hi from match_scores group by city having count(*)>=5 order by hi desc limit 10"):
        print(f"  {city or '?':6s} 扫{n:4d}  ≥60分:{hi:3d}  命中率{hi*100//max(n,1)}%")

    print("\n【4】最高分的 15 个岗位（当前画像下最匹配的在招/历史扫描）")
    for r in q("select total,verdict,company,title,city,salary,score_low,top_gap from match_scores order by total desc limit 15"):
        gap = ('缺口:'+r[6]) if r[6] else ''
        print(f"  {r[0]:3d}分 {r[1]:13s} {r[2][:12]:12s} {r[3][:24]:24s} {r[4]} {r[5]} {gap}")

    print("\n【5】高分被拦复盘（引擎≥60 但决策链给了 skip/reject——过滤规则 vs 评分冲突）")
    rows = q("select job_key,company,title,total,salary,keyword from match_scores where total>=60 order by total desc")
    con2 = con
    clash = []
    for key, comp, title, tot, sal, kw in rows:
        ev = con2.execute("select type,payload from events where payload like ? order by timestamp desc limit 1",
                          (f'%{comp}%{title[:12]}%',)).fetchone()
        if ev:
            d = json.loads(ev[1])
            dec = d.get('decision', ev[0])
            if 'skip' in str(dec) or 'reject' in str(d.get('reason', '')):
                clash.append((tot, comp, title, ev[0], str(d.get('reason', ''))[:40]))
    for c5 in clash[:12]:
        print(f"  {c5[0]:3d}分 {c5[1][:12]:12s} {c5[2][:22]:22s} [{c5[3]}] {c5[4]}")
    if not clash:
        print("  （无冲突——高分岗要么真投了要么压根没被拦）")


if __name__ == "__main__":
    con, scored, seen = main()
    print(f"[ok] 本次评分写入 {scored} 条")
    report(con)

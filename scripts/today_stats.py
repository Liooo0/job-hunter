#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""今日投递体检：真实投递量 / 分平台 / 分时段 / 7日趋势 / HR侧 / cron / 待审草稿"""
import datetime
import json
import os
import sqlite3

JH = os.path.expanduser("~/projects/job-hunter")
DB = os.path.join(JH, "ab_experiment.db")
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
today = datetime.date.today().isoformat()
REAL = "status in ('UNCERTAIN','APPLIED','VERIFIED')"

print(f"═══ 现在 {datetime.datetime.now():%Y-%m-%d %H:%M (%A)} ═══\n")

print("═══ 今日投递（分平台）═══")
rows = c.execute(f"""select platform,
        count(*) n, sum({REAL}) real_n, sum(status='SKIPPED') skip_n
    from applications_v2 where date(created_at)=? group by platform order by n desc""", (today,)).fetchall()
if not rows:
    print("  今日 0 行 —— 该小时的任务没跑，或还没到点")
for r in rows:
    print(f"  {str(r['platform'] or '(空)'):10} 总行 {r['n']:4}   真投 {r['real_n']:4}   跳过 {r['skip_n']:4}")
tot = c.execute(f"select count(*) from applications_v2 where date(created_at)=? and {REAL}", (today,)).fetchone()[0]
print(f"  → 今日真投合计: {tot}")

print("\n═══ 今日分时段（哪几轮真跑了）═══")
hs = c.execute(f"""select substr(created_at,12,2) h, platform, count(*) n
    from applications_v2 where date(created_at)=? and {REAL}
    group by h, platform order by h""", (today,)).fetchall()
for r in hs:
    print(f"  {r['h']} 点   {str(r['platform']):10} {r['n']:3}")
if not hs:
    print("  （无）")

print("\n═══ 近 7 天趋势（真投）═══")
for r in c.execute(f"""select date(created_at) d, count(*) n from applications_v2
    where date(created_at) >= date('now','-7 day') and {REAL} group by d order by d"""):
    bar = "█" * min(60, r['n'] // 5)
    print(f"  {r['d']}  {r['n']:5}  {bar}")

print("\n═══ 今日分平台明细（看跳过原因分布）═══")
for r in c.execute("""select platform, decision, count(*) n from applications_v2
    where date(created_at)=? group by platform, decision order by n desc limit 8""", (today,)):
    print(f"  {str(r['platform']):10} {str(r['decision'])[:20]:22} {r['n']}")

print("\n═══ HR 侧（近 45 天）═══")
for col in ("read", "replied", "interview", "offer"):
    try:
        n = c.execute(f"select count(*) from applications_v2 where {col}=1 and date(created_at)>=date('now','-45 day')").fetchone()[0]
        print(f"  {col:10} {n}")
    except sqlite3.Error as e:
        print(f"  {col:10} 查询失败: {str(e)[:50]}")

print("\n═══ 投递相关 cron 今天的状态 ═══")
p = os.path.expanduser("~/.hermes/cron/jobs.json")
if os.path.exists(p):
    d = json.load(open(p))
    jobs = d if isinstance(d, list) else d.get("jobs", [])
    for j in jobs:
        n = j.get("name", "")
        if any(k in n for k in ("投递", "HR回复", "收尾")):
            print(f"  {n[:32]:34} {str(j.get('schedule','')):24} 上次 {str(j.get('last_run_at'))[:16]}  {j.get('last_status')}")

print("\n═══ 待审核的 HR 回复草稿 ═══")
for rel in ("data/reply_pending.json", "data/reply_review.lock", "data/reply_stats.json"):
    fp = os.path.join(JH, rel)
    if not os.path.exists(fp):
        print(f"  {rel}: 不存在")
        continue
    try:
        d = json.load(open(fp))
        if isinstance(d, list):
            pend = sum(1 for x in d if isinstance(x, dict) and x.get("status") == "pending")
            print(f"  {rel}: 共 {len(d)} 条，其中待审 {pend} 条")
            for x in d[:4]:
                if isinstance(x, dict) and x.get("status") == "pending":
                    print(f"     - {x.get('job','?')} / {str(x.get('hr_message'))[:40]}")
        else:
            print(f"  {rel}: {json.dumps(d, ensure_ascii=False)[:140]}")
    except (OSError, ValueError) as e:
        print(f"  {rel}: 读取失败 {str(e)[:40]}")

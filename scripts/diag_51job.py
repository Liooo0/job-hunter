#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""51job 零回复诊断：投的什么岗、有没有重复、状态口径、简历是否真送达"""
import datetime
import sqlite3

c = sqlite3.connect("ab_experiment.db")
c.row_factory = sqlite3.Row

print("═══ 1. 51job 的表结构（能看到哪些字段）═══")
cols = [r[1] for r in c.execute("PRAGMA table_info(applications_v2)")]
print("  ", ", ".join(cols))

print("\n═══ 2. 51job 全量状态分布 ═══")
for r in c.execute("""select status, decision, count(*) n from applications_v2
    where platform like '%51%' group by status, decision order by n desc limit 12"""):
    print(f"  status={str(r['status']):12} decision={str(r['decision']):12} {r['n']:6}")

print("\n═══ 3. 总量 / 去重后真实岗位数 ═══")
tot = c.execute("select count(*) from applications_v2 where platform like '%51%'").fetchone()[0]
try:
    uniq = c.execute("select count(distinct job_id) from applications_v2 where platform like '%51%'").fetchone()[0]
except sqlite3.Error:
    uniq = None
print(f"  总行数 {tot}   去重岗位 {uniq}")
if uniq:
    print(f"  → 平均每个岗位被投 {tot/uniq:.1f} 次")

print("\n═══ 4. 最近 40 条 51job 投递的岗位（看是什么类型）═══")
q = """select created_at, company, title, salary, city, status from applications_v2
       where platform like '%51%' and status in ('UNCERTAIN','APPLIED','VERIFIED')
       order by created_at desc limit 40"""
for r in c.execute(q):
    print(f"  {str(r['created_at'])[5:16]} | {str(r['company'])[:16]:16} | {str(r['title'])[:28]:28} | {str(r['salary'])[:10]:10} | {str(r['city'])[:6]}")

print("\n═══ 5. 关键词自检：投的岗有多少真跟 AI/数据/开发沾边 ═══")
kw = ["AI", "算法", "数据", "开发", "工程师", "Python", "模型", "智能", "产品", "运营", "实施", "测试", "运维"]
rows = c.execute("""select title from applications_v2
    where platform like '%51%' and status in ('UNCERTAIN','APPLIED','VERIFIED')
      and date(created_at) >= date('now','-14 day')""").fetchall()
titles = [str(r["title"] or "") for r in rows]
print(f"  近 14 天 {len(titles)} 条，标题命中分布：")
for k in kw:
    n = sum(1 for t in titles if k.lower() in t.lower())
    if n:
        print(f"    {k:8} {n:5}  ({n*100//max(1,len(titles))}%)")

print("\n═══ 6. 今日 51job 明细（14 条）═══")
for r in c.execute("""select created_at, company, title, salary, city, decision, status
    from applications_v2 where platform like '%51%' and date(created_at)=date('now')
    order by created_at"""):
    print(f"  {str(r['created_at'])[11:19]} | {str(r['company'])[:18]:18} | {str(r['title'])[:26]:26} | {str(r['salary'])[:10]:10} | {str(r['decision'])}/{str(r['status'])}")

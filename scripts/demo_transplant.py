#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""移植 MVP 演示 —— 跑真实数据，看这 6 个模块能抓到什么。

用法：
    env PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 scripts/demo_transplant.py

数据来源（全部真实，不编造）：
  · 简历：~/projects/resume-kami/resume-E-ai-delivery.html（当"用户亲手写的源事实"）
  · 故事库：data/story-bank.md（MVP 种子，数字均来自简历或已标状态）
  · 投递库：ab_experiment.db 的 applications_v2
"""
import html
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import block_g as bg              # noqa: E402
import funnel_stats as fs         # noqa: E402
import interview_prep as ip       # noqa: E402
import liveness as lv             # noqa: E402
import provenance as pv           # noqa: E402
import repost_detect as rd        # noqa: E402

RESUME_HTML = Path.home() / 'projects/resume-kami/resume-E-ai-delivery.html'
STORY_BANK = ROOT / 'data/story-bank.md'
DB = ROOT / 'ab_experiment.db'


def hr(title):
    print()
    print('=' * 78)
    print(title)
    print('=' * 78)


def resume_text() -> str:
    if not RESUME_HTML.exists():
        return ""
    h = RESUME_HTML.read_text(encoding='utf-8', errors='ignore')
    h = re.sub(r'<style.*?</style>', ' ', h, flags=re.S)
    h = re.sub(r'<script.*?</script>', ' ', h, flags=re.S)
    h = re.sub(r'<br\s*/?>', '\n', h)
    h = re.sub(r'</(p|div|li|h[1-6])>', '\n', h)
    t = re.sub(r'<[^>]+>', ' ', h)
    t = html.unescape(t)
    t = re.sub(r'[ \t]+', ' ', t)
    return re.sub(r'\n\s*\n+', '\n', t).strip()


def q(sql, args=()):
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def main():
    cv = resume_text()
    print(f"源事实：{RESUME_HTML.name}（{len(cv)} 字）")
    print(f"故事库：{STORY_BANK.relative_to(ROOT)}")
    print(f"投递库：{DB.name}")

    # ───────── ① 四态溯源 ─────────
    hr("① 四态溯源：故事库里的数字，有多少能在你亲手写的简历里找到佐证")
    results = pv.check_story_bank(STORY_BANK, [RESUME_HTML])
    if not results:
        print("  （故事库里没抽到数字主张）")
    counts = {}
    for r in results:
        counts[r['state']] = counts.get(r['state'], 0) + 1
        mark = {pv.EXISTING: '✅', pv.SUPPORTED: '🔶', pv.DERIVED: '❔',
                pv.CANNOT: '⛔'}.get(r['state'], '·')
        print(f"  {mark} [{r['state']}] 《{r['story'][:26]}》 数字 {r['numbers']}")
        print(f"       {r['why'][:76]}")
        print(f"       Provenance 字段：{r['provenance_field']}")
    print()
    print("  分布：" + " / ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("  ⚠️ 只有 existing / supported 才允许出现在对外话术里；"
          "user-cannot-confirm 是硬覆盖，永不自愈。")

    # ───────── ② 出口硬数字闸 ─────────
    hr("② 出口硬数字闸：要发出去的话里的数字，没佐证的整条剔除")
    samples = [
        "我独立交付过两套 AI 系统，累计处理 3万+ 条岗位数据，系统可用率 99.9%。",
        "我把客户咨询响应时效从 4 小时压到 5 分钟，转化率提升 150%。",
        "熟悉 Python / RAG / Dify，做过 15 组边界问题实测。",
    ]
    for s in samples:
        g = pv.gate_outgoing_text(s, {'resume_E.html': cv}, story_bank_path=STORY_BANK)
        print(f"  原句：{s[:52]}…")
        if g['blocked']:
            print(f"    ⛔ 剔除数字：{g['blocked']}   ✅ 有佐证：{g['ok']}")
            print(f"    处理后：{g['stripped_text'][:60]}…")
        else:
            print(f"    ✅ 全部有佐证：{g['ok']}")

    # ───────── ③ 岗位存活 ─────────
    hr("③ 岗位存活判定：明确过期才拦，反爬插页绝不判过期")
    pages = [
        ("中文闭站", "该职位已过期，感谢关注。您可以看看其他相似职位。"),
        ("法文闭站（弯撇号 U+2019）", "Cette offre n\u2019est plus disponible."),
        ("真·招满", "the job you are trying to apply for has been filled"),
        ("反爬插页", "Just a moment... Enable JavaScript and cookies to continue"),
        ("中文风控页", "访问过于频繁，请完成安全验证后重试"),
        ("列表页", "共 128 个职位" + "x" * 300),
        ("活岗（含'填表'句）",
         "Once the application form has been filled you will receive a confirmation email. "
         "We are hiring a Python engineer to build RAG pipelines in Shenzhen. Responsibilities "
         "include knowledge base design, vector search tuning and Agent workflow delivery. "
         "Requirements: 3+ years Python, hands-on LLM API experience, strong communication. "
         "apply now to join our team building the next generation of internal tooling."),
    ]
    for label, text in pages:
        st, why = lv.classify(text)
        filt = "→ 可写历史/拉黑" if lv.should_filter_out(st) else "→ 保持不确定（不动）"
        print(f"  {label:22s} {lv.describe(st):30s} {filt}")

    # ───────── ④ Block G ─────────
    hr("④ Block G 岗位真实性：只摆信号 + 给合理解释，且不影响评分")
    row = q("""SELECT company, title, salary FROM applications_v2
               WHERE (company LIKE '%餐饮%' OR company LIKE '%人力资源%')
               AND status IN ('UNCERTAIN','APPLIED','VERIFIED')
               ORDER BY created_at DESC LIMIT 1""")
    if row:
        c, t, s = row[0]['company'], row[0]['title'], row[0]['salary']
        print(f"  实测样本（来自你的投递库）：{c[:24]} | {t[:30]} | {s}")
        a = bg.assess(posting_age_days=None, apply_button_active=None, jd_text=t,
                      repost_count_90d=1, role_company_fit="岗位与公司主营不匹配")
        print()
        print("  " + a.report().replace("\n", "\n  "))
    a2 = bg.assess(posting_age_days=95, apply_button_active=False, jd_text='前沿科技 赋能生态 拥抱未来',
                   repost_count_90d=3)
    print()
    print(f"  对照样本（挂 95 天 + 按钮不可点 + 反复发布）→ {a2.label}，score_impact={a2.score_impact}")

    # ───────── ⑤ 重发检测 ─────────
    hr("⑤ 改标题重发检测：抓'同一家公司同一岗位反复挂'")
    since = (date.today() - timedelta(days=90)).isoformat()
    rows = q("""SELECT company, title, job_id AS url, date(created_at) AS first_seen, lower(status) AS status
                FROM applications_v2
                WHERE date(created_at) >= ? AND job_id IS NOT NULL AND job_id != ''
                  AND company IS NOT NULL AND company != ''""", (since,))
    print(f"  窗口内样本：{len(rows)} 行（90 天）")
    clusters = rd.find_reposts(rows, eligible_statuses=('uncertain', 'applied', 'verified'))
    if not clusters:
        print("  （未发现重发簇 —— 也可能是同一标题只投过一次，符合预期）")
    for c in clusters[:10]:
        print(rd.explain(c))
    print(f"  合计 {len(clusters)} 个重发簇")

    # ───────── ⑥ 漏斗统计 ─────────
    hr("⑥ 漏斗统计诚实规则：右删失 / n<20 不下结论 / 基准带年份")
    today = date.today().isoformat()
    n_today = q("""SELECT COUNT(*) n FROM applications_v2
                   WHERE date(created_at)=? AND status IN ('UNCERTAIN','APPLIED','VERIFIED')""", (today,))[0]['n']
    n_total = q("""SELECT COUNT(*) n FROM applications_v2
                   WHERE status IN ('UNCERTAIN','APPLIED','VERIFIED')""")[0]['n']
    n_offer = q("SELECT COUNT(*) n FROM applications_v2 WHERE offer=1")[0]['n']
    n_interview = q("SELECT COUNT(*) n FROM applications_v2 WHERE interview=1")[0]['n']
    n_replied = q("SELECT COUNT(*) n FROM applications_v2 WHERE replied=1")[0]['n']
    print(f"  今日投出 {n_today} | 累计真实投出 {n_total} | 有回复 {n_replied} | 有面试 {n_interview} | offer {n_offer}")
    print()
    m = fs.median_with_censoring([], censored_count=max(0, n_total - n_replied), label="回复等待天数")
    print(f"  回复等待中位数：{m['median']}（n={m['n']}，删失 {m['censored']}）")
    print(f"    {m['note']}")
    cal = fs.calibrate(n_replied / n_total if n_total else None, n_total,
                       {'low': 0.05, 'high': 0.10, 'year': 2026, 'source': '方向性参考'})
    for n in cal['notes']:
        print(f"    {n}")
    if cal.get('position'):
        print(f"    → 位置判断：{cal['position']}")

    # ───────── ⑦ 面试准备 ─────────
    hr("⑦ 面试准备：按受众分桶 + 故事映射 + 缺口显性化")
    job = q("""SELECT company, title, keyword FROM applications_v2
               WHERE status IN ('UNCERTAIN','APPLIED','VERIFIED')
                 AND (title LIKE '%AI%' OR title LIKE '%大模型%' OR title LIKE '%智能体%')
               ORDER BY created_at DESC LIMIT 1""")
    stories = pv.load_story_bank(STORY_BANK)
    if job:
        c, t, kw = job[0]['company'], job[0]['title'], job[0]['keyword'] or ''
        print(f"  样本岗位：{c[:26]} | {t[:34]}")
        rep = ip.build_prep(c, t, f"{t} {kw} 负责 RAG 知识库 大模型 Agent 交付 Python",
                            stories=stories, cv_text=cv, stated_comp="",
                            stage_count=3)
        print()
        print(rep.render())
        print()
        print(f"  故事库现有 {len(stories)} 条；本次映射出 {len([m for m in rep.mapping if m.fit != 'none'])} 条命中、"
              f"{len(rep.gaps)} 个缺口。")

    hr("小结")
    print("  这 6 个模块全部是**只读**的：不投递、不发消息、不改库。")
    print("  验证它们有效的方式：看上面每一条是不是你原先系统抓不到的。")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""51job 自动投递 v4 — 升级: 9223 + evaluate_job(L2决策) + record_application落库 + 日限50

复用 v3 的抓取/点击核心(sensorsdata结构化卡片), 决策层从 score_jd 换成 job_decision.evaluate_job
规则全平台统一: 底薪≥8K / 薪资≤30K / 排除销售标注狼性实习
独立限额: 50/天 (不占Boss的150)
"""
import argparse, json, time, random, sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from DrissionPage import ChromiumPage
from job_decision import evaluate_job
from store import record_application


def in_night_window() -> bool:
    """夜间禁投 22:00-8:00（与 boss_apply 同规则，封号红线）"""
    h = datetime.now().hour
    return h >= 22 or h < 8

CITY_CODES = {
    "深圳": "040000", "广州": "030200", "北京": "010000", "上海": "020000",
    "东莞": "030800", "佛山": "030600", "惠州": "031600", "珠海": "030400",
    "杭州": "080200", "成都": "090200", "武汉": "170200", "南京": "060200",
    "苏州": "060800", "西安": "110200", "天津": "030500", "重庆": "040200",
}
DAILY_LIMIT = 80  # 2026-09-10: 用户要求加量(正常50); 平台侧51job上限宽松
PORT = 9223


def get_cards(tab):
    return tab.run_js("""
        return Array.from(document.querySelectorAll('.joblist-item')).map(c => {
            var sd = {};
            try { sd = JSON.parse(c.querySelector('[sensorsdata]')?.getAttribute('sensorsdata') || '{}'); } catch(e) {}
            var btn = c.querySelector('button.btn.apply');
            var companyEl = c.querySelector('a[href*="co"]') || c.querySelector('a');
            return {
                jobId: sd.jobId || '',
                title: sd.jobTitle || '',
                salary: sd.jobSalary || '',
                area: sd.jobArea || '',
                year: sd.jobYear || '',
                degree: sd.jobDegree || '',
                company: (companyEl?.innerText || '').trim() || sd.brandName || '',
                btnText: (btn?.innerText || '').trim(),
            };
        }).filter(x => x.jobId);
    """) or []


def click_apply_and_check(tab, job_id):
    for _ in range(2):
        tab.run_js(f"""
            var cards = document.querySelectorAll('.joblist-item');
            for (var c of cards) {{
                var sd = c.querySelector('[sensorsdata]');
                if (!sd) continue;
                try {{
                    var d = JSON.parse(sd.getAttribute('sensorsdata'));
                    if (String(d.jobId) === "{job_id}") {{
                        var btn = c.querySelector('button.btn.apply');
                        if (!btn) return;
                        c.scrollIntoView({{block:'center'}});
                        btn.click();
                    }}
                }} catch(e) {{}}
            }}
        """)
        time.sleep(2.5)
        state = tab.run_js(f"""
            var cards = document.querySelectorAll('.joblist-item');
            for (var c of cards) {{
                var sd = c.querySelector('[sensorsdata]');
                if (!sd) continue;
                try {{
                    var d = JSON.parse(sd.getAttribute('sensorsdata'));
                    if (String(d.jobId) === "{job_id}") {{
                        return (c.querySelector('button.btn.apply')?.innerText || '').trim();
                    }}
                }} catch(e) {{}}
            }}
            return '';
        """)
        if "已申请" in state or "已投递" in state:
            return state
    return ""


def run_city_keyword(page, tab, city, keyword, count, seen, today_applied):
    city_code = CITY_CODES.get(city)
    if not city_code:
        return 0, 0
    url = f"https://we.51job.com/pc/search?keyword={quote(keyword)}&jobArea={city_code}&degree=04&workyear=02,03"
    applied, skipped = 0, 0
    page_num = 1
    empty_streak = 0

    print(f"\n{'='*50}\n📍 {city} | 🔍 {keyword} | 🎯 {count}\n{'='*50}")
    tab.get(url); time.sleep(4 + random.uniform(0, 2))

    if "login" in tab.url.lower():
        print("⚠️ 未登录 51job"); return 0, 0

    while applied < count and empty_streak < 3 and page_num <= 6:
        if page_num > 1:
            tab.get(f"{url}&pageNum={page_num}"); time.sleep(3 + random.uniform(0, 2))
        tab.run_js("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(1.5)
        tab.run_js("window.scrollTo(0, 0);"); time.sleep(1)

        cards = get_cards(tab)
        if not cards:
            empty_streak += 1; page_num += 1; continue

        pending = [c for c in cards if c["jobId"] not in seen and "已申请" not in c["btnText"] and "已投递" not in c["btnText"]]
        if not pending:
            page_num += 1; empty_streak += 1; continue
        empty_streak = 0
        print(f"  第{page_num}页 | {len(cards)}卡 | 待处理 {len(pending)}")

        for c in pending:
            if applied >= count or today_applied >= DAILY_LIMIT:
                break
            if in_night_window():
                print("  🌙 夜间禁投，停止本页处理")
                return applied, skipped
            seen.add(c["jobId"])

            # 校招/应届/实习一律不投（用户 2026-09-09 定稿：应届生岗也不投了）
            # 命中: XX届 / 校招 / 应届生 / 实习
            t = c["title"] or ""
            import re as _re
            if (_re.search(r"[0-9０-９]{2}届", t) or "校招" in t or "应届" in t
                    or "实习" in t or "管培生" in t or "培训生" in t):
                skipped += 1
                print(f"  [🚫校招/应届/实习] {t[:35]} | {c['salary']}")
                continue

            # L2 决策(全平台统一规则) — 与 boss_apply 同款三段闸
            reason = ""
            block = False
            try:
                dec = evaluate_job(c["company"] or "", c["title"], "", c["salary"], city=city)
                if getattr(dec, "action", None) == "REJECT":
                    reason = getattr(dec, "reason", "L2拒绝")
                    block = True
                else:
                    reason = f"L2:({getattr(dec, 'priority', '?')}|{getattr(dec, 'salary_band', '?')})"
            except Exception as e:
                print(f"  [⚠️决策器异常] {c['title'][:30]}: {e}")
                continue
            # L3 语义层(销售/标注伪装)
            if not block:
                try:
                    import semantic_parser as _SP
                    sp_reason = _SP.gate(c["title"], "", c["company"] or "")
                    if sp_reason:
                        reason = f"语义:{sp_reason}"
                        block = True
                except Exception as e:
                    print(f"  [⚠️语义层异常] {e}")
            # Plan Router(岗位错位闸)
            if not block:
                try:
                    from plan_router import route_plan
                    from job_decision import parse_salary_low
                    _pr = route_plan(c["company"] or "", c["title"], "",
                                     parse_salary_low(c["salary"]), city=city)
                    if getattr(_pr, "plan", None) == "NO_PLAN":
                        reason = f"路由:{getattr(_pr, 'reason', 'NO_PLAN')}"
                        block = True
                except Exception as e:
                    print(f"  [⚠️路由异常] {e}")
            if block:
                skipped += 1
                print(f"  [🚫] {c['title'][:35]} | {c['salary']} → {reason}")
                continue

            print(f"  [✅{c['title'][:30]}] | {c['salary']} | {c['company'][:15]}")
            state = click_apply_and_check(tab, c["jobId"])
            if "已申请" in state or "已投递" in state:
                applied += 1
                today_applied += 1
                try:
                    record_application(
                        platform="51job", city=city, company=c["company"] or "未知",
                        title=c["title"], salary=c["salary"], keyword=keyword,
                        score=0, resume_version="E", decision="ALLOW",
                        status="UNCERTAIN", reason=str(reason)[:80],
                        verified=0, event_type="apply", event_error=None,
                        extra_payload={"jobId": c["jobId"], "area": c["area"]},
                        gates=None, greeting_template_id=None,
                    )
                except Exception as e:
                    print(f"    ⚠️ 落库失败: {e}")
                print(f"    ✅ 已投递 ({applied}/{count}, 今日{today_applied}/{DAILY_LIMIT})")
            else:
                skipped += 1
                print(f"    ❌ 按钮状态: {state}")
            time.sleep(2 + random.uniform(0, 2))
        page_num += 1
    return applied, skipped


def main():
    import config
    args = sys.argv[1:]
    cities = ["深圳", "广州", "杭州", "成都"]
    keywords = ["AI应用工程师", "AI实施", "AI解决方案", "AI Agent", "AI智能体", "大模型应用", "LLM应用", "RPA开发"]
    count = 10
    if "--cities" in args:
        cities = args[args.index("--cities")+1].split(",")
    if "--jobs" in args:
        keywords = args[args.index("--jobs")+1].split(",")
    if "--count" in args:
        count = int(args[args.index("--count")+1])

    print(f"╔══ 51job v4 ══ 城市{len(cities)} 词{len(keywords)} 上限{DAILY_LIMIT}/天 ══╗")
    try:
        page = ChromiumPage(PORT)
    except Exception as e:
        print(f"❌ Chrome 未连接(端口{PORT}): {e}")
        return
    tab = page.new_tab("about:blank")
    seen = set()
    # 2026-09-10 修复: 原为进程内计数, 多轮跑会突破日限额。
    # 改为启动时从 DB 读当日已投数, 跨轮累计受控。
    today_applied = 0
    try:
        import sqlite3
        from datetime import date as _date
        _con = sqlite3.connect(str(Path(__file__).parent / 'ab_experiment.db'))
        _today = _date.today().isoformat()
        today_applied = _con.execute(
            "SELECT COUNT(*) FROM applications_v2 WHERE platform='51job' "
            "AND date(created_at)=? AND status IN ('UNCERTAIN','APPLIED','VERIFIED')",
            (_today,)).fetchone()[0] or 0
        _con.close()
        print(f"📊 今日已投 {today_applied}/{DAILY_LIMIT} 条(DB统计)")
        if today_applied >= DAILY_LIMIT:
            print("🛑 今日额度已满，退出")
            return
    except Exception as e:
        print(f"⚠️ DB统计失败({e})，按0计")
    total_a = total_s = 0
    try:
        for city in cities:
            if today_applied >= DAILY_LIMIT or in_night_window(): break
            for kw in keywords:
                if today_applied >= DAILY_LIMIT: break
                if in_night_window():
                    print("  🌙 已到夜间禁投时段(22:00-8:00)，本轮收工")
                    break
                try:
                    a, s = run_city_keyword(page, tab, city, kw, count, seen, today_applied)
                    total_a += a; total_s += s; today_applied += a
                except Exception as e:
                    print(f"  ❌ {city}/{kw}: {e}")
                if today_applied < DAILY_LIMIT:
                    rest = 15 + random.uniform(0, 10)
                    print(f"  ☕ 休息 {rest:.0f}s (今日 {today_applied}/{DAILY_LIMIT})")
                    time.sleep(rest)
    finally:
        tab.close()
    print(f"╔══ 51job完成 ══ ✅ {total_a} 投 | ⏭️ {total_s} 跳 ══╗")


if __name__ == "__main__":
    main()

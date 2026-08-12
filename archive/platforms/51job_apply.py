#!/usr/bin/env python3
"""51job 全自动投递 v3 — fetch_jd 不再切tab，只用搜索列表卡片的 salary+meta 打分"""

import argparse, json, time, random, sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from DrissionPage import ChromiumPage

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))
from shared import load_config, score_jd, smart_filter, load_log, save_log

# 城市代码
CITY_CODES = {
    "深圳": "040000", "广州": "030200", "北京": "010000", "上海": "020000",
    "东莞": "030800", "佛山": "030600", "惠州": "031600", "珠海": "030400",
    "杭州": "080200", "成都": "090200", "武汉": "170200", "南京": "060200",
    "苏州": "060800", "西安": "110200", "天津": "030500", "重庆": "040200",
}


def parse_args(cfg):
    p = argparse.ArgumentParser()
    p.add_argument("--daily", action="store_true")
    p.add_argument("--job", default=None)
    p.add_argument("--jobs", default=None)
    p.add_argument("--city", default=None)
    p.add_argument("--cities", default=None)
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--min-score", type=int, default=None)
    return p.parse_args()


def get_cards(tab):
    return tab.run_js("""
        return Array.from(document.querySelectorAll('.joblist-item')).map(c => {
            var sd = {};
            try { sd = JSON.parse(c.querySelector('[sensorsdata]')?.getAttribute('sensorsdata') || '{}'); } catch(e) {}
            var btn = c.querySelector('button.btn.apply');
            return {
                jobId: sd.jobId || '',
                title: sd.jobTitle || '',
                salary: sd.jobSalary || '',
                area: sd.jobArea || '',
                year: sd.jobYear || '',
                degree: sd.jobDegree || '',
                btnText: (btn?.innerText || '').trim(),
            };
        }).filter(x => x.jobId);
    """) or []


def click_apply_and_check(tab, job_id):
    """点投递按钮，最多点2次，返回按钮文字"""
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
        if "已申请" in state:
            return state
    return ""


def run_city_keyword(page, tab, city, keyword, count, min_score, cfg, log, seen):
    city_code = CITY_CODES.get(city)
    if not city_code:
        print(f"  ❌ 不支持城市: {city}")
        return 0, 0

    # 51job URL: degree=04(本科) & workyear=02,03(1年以下+1-3年=排除应届/实习筛选)
    search_url = f"https://we.51job.com/pc/search?keyword={quote(keyword)}&jobArea={city_code}&degree=04&workyear=02,03"
    applied, skipped = 0, 0
    page_num = 1
    max_pages = 6
    consecutive_empty = 0

    print(f"\n{'='*50}")
    print(f"📍 {city} | 🔍 {keyword} | 🎯 {count}")
    print(f"{'='*50}")

    tab.get(search_url); time.sleep(4 + random.uniform(0, 2))

    if "login" in tab.url:
        print("⚠️  未登录 — 请在Chrome 2中登录 51job.com 后重新运行")
        return 0, 0

    while applied < count and consecutive_empty < 3 and page_num <= max_pages:
        tab.get(f"{search_url}&pageNum={page_num}")
        time.sleep(3 + random.uniform(0, 2))
        tab.run_js("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(1.5)
        tab.run_js("window.scrollTo(0, 0);"); time.sleep(1)

        cards = get_cards(tab)
        if not cards:
            consecutive_empty += 1; page_num += 1
            continue

        pending = [c for c in cards if c["jobId"] not in seen and "已申请" not in c["btnText"]]
        if not pending:
            page_num += 1; consecutive_empty += 1
            continue

        consecutive_empty = 0
        print(f"  第{page_num}页 | {len(cards)}卡 | 待处理 {len(pending)}")

        for c in pending:
            if applied >= count: break
            seen.add(c["jobId"])

            meta = f"{c['area']} {c['salary']} {c['year']} {c['degree']}"
            # 用标题+元信息打分（不切详情页）
            score, reason = score_jd(c["title"], meta, cfg)
            smart, s_reason = smart_filter("", c["title"], meta, c["salary"], score, cfg, city)
            if smart != score:
                if smart == 0:
                    print(f"  [🔴过滤] {c['title'][:35]} | {c['salary']} → {s_reason}")
                    skipped += 1; continue
                score, reason = smart, str(reason)+"、"+str(s_reason)

            print(f"  [{score:3d}分] {c['title'][:35]} ({c['salary']}/{c['area']})")

            if score < min_score:
                skipped += 1; continue

            state = click_apply_and_check(tab, c["jobId"])
            if "已申请" in state:
                applied += 1
                log["applied"].append({"jobId": c["jobId"], "job": c["title"],
                    "salary": c["salary"], "area": c["area"], "score": score,
                    "city": city, "keyword": keyword, "time": datetime.now().isoformat()})
                save_log(log, SKILL_DIR / f"51job-{city}-log.json")
                print(f"    ✅ 已投递 ({applied}/{count})")
            else:
                skipped += 1
                log["skipped"].append({"jobId": c["jobId"], "job": c["title"],
                    "reason": f"btn={state}"})
                save_log(log, SKILL_DIR / f"51job-{city}-log.json")

            time.sleep(1.5 + random.uniform(0, 1))
        page_num += 1

    return applied, skipped


def main():
    cfg = load_config(SKILL_DIR)
    args = parse_args(cfg)

    if args.daily or (not args.job and not args.jobs):
        cities = (args.cities or ",".join(cfg.get("target_cities", ["深圳"]))).split(",")
        cities = [c.strip() for c in cities if c.strip()]
        keywords = (args.jobs or ",".join(cfg.get("search_keywords", []))).split(",")
        keywords = [k.strip() for k in keywords if k.strip()]
    elif args.jobs:
        keywords = [k.strip() for k in args.jobs.split(",") if k.strip()]
        cities = [c.strip() for c in (args.cities or "深圳").split(",")]
    else:
        keywords = [args.job]; cities = [args.city or "深圳"]

    count = args.count or cfg.get("default_count", 30)
    min_score = args.min_score or cfg.get("min_score", 20)
    DAILY_LIMIT = 120

    print(f"""
╔══════════════════════════════════════╗
║  🤖 51job v3                         ║
╠══════════════════════════════════════╣
║  城市: {', '.join(cities[:4])}...    ║
║  搜索词: {len(keywords)} 个          ║
║  上限: {DAILY_LIMIT} | min分: {min_score}  ║
╚══════════════════════════════════════╝
""")

    page = ChromiumPage(9222)
    tab = page.new_tab("about:blank")
    total_a, total_s = 0, 0

    for city in cities:
        if total_a >= DAILY_LIMIT: break
        for keyword in keywords:
            if total_a >= DAILY_LIMIT: break
            try:
                a, s = run_city_keyword(page, tab, city, keyword, count, min_score, cfg,
                                        {"applied": [], "skipped": []}, set())
                total_a += a; total_s += s
            except Exception as e:
                print(f"  ❌ {city}/{keyword}: {e}")
            if city != cities[-1] or keyword != keywords[-1]:
                rest = 20 + random.uniform(0, 10)
                print(f"\n  ☕ 休息 {rest:.0f}s (已投 {total_a}/{DAILY_LIMIT})")
                time.sleep(rest)

    print(f"""
╔══════════════════════════════════════╗
║  51job 全部完成                     ║
║  ✅ 投递: {total_a}  ⏭️ 跳过: {total_s}    ║
╚══════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()

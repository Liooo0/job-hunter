#!/usr/bin/env python3
"""猎聘 自动投递 v1

⚠️ DOM 选择器需首次实测时校准（猎聘反爬/加密较频繁）。
⚠️ 猎聘的"立即沟通"是 IM 对话而非一键投递，需结合在线简历。
⚠️ 猎聘以猎头推荐为主，批量投递效果不如其他平台，建议作为补充。
"""

import argparse, json, time, random, re, sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from DrissionPage import ChromiumPage
from shared import load_config, load_log, save_log, score_jd, smart_filter

SKILL_DIR = Path(__file__).parent

# 猎聘城市 code（dgsearch 参数，=1 表示全国）
# 猎聘 URL: https://www.liepin.com/zhaopin/?city=<code>&key=<keyword>
CITY_CODES = {
    "深圳": "050090000", "广州": "050020000", "北京": "010000000", "上海": "020000000",
    "东莞": "050180000", "佛山": "050040000", "惠州": "053270000", "珠海": "050140000",
    "杭州": "070020000", "成都": "280020000", "武汉": "170020000", "南京": "060020000",
}


def parse_args(cfg):
    p = argparse.ArgumentParser(description="猎聘 自动投递")
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
        var selectors = ['.job-list-item', '.job-card-box', '.job-recommend',
                         '[class*="job-list"] > div', '.search-job-result li',
                         '.job-info'];
        var out = [];
        for (var s of selectors) {
            var els = document.querySelectorAll(s);
            if (els.length > 5) {
                els.forEach(function(el) {
                    var titleEl = el.querySelector('[class*="title"], [class*="name"], .job-title-box, h3');
                    var salaryEl = el.querySelector('[class*="salary"], .job-salary');
                    var companyEl = el.querySelector('[class*="company"], [class*="com-name"], .company-name');
                    var linkEl = el.querySelector('a[href*="job"]') || el.querySelector('a');
                    out.push({
                        title: (titleEl?.textContent || '').trim(),
                        salary: (salaryEl?.textContent || '').trim(),
                        company: (companyEl?.textContent || '').trim(),
                        href: linkEl?.href || '',
                    });
                });
                if (out.length > 0) return out;
            }
        }
        return out;
    """) or []


def click_chat_button(tab, idx):
    """猎聘卡片 — 点击"立即沟通"按钮"""
    tab.run_js(f"""
        var cards = document.querySelectorAll('.job-list-item, .job-card-box, [class*="job-list"] > div');
        if (!cards[{idx}]) return;
        cards[{idx}].scrollIntoView({{block:'center'}});
        cards[{idx}].click();
    """)
    time.sleep(2)
    tab.run_js("""
        var btns = document.querySelectorAll('button, a');
        for (var b of btns) {
            var t = (b.textContent||'').trim();
            if (t.indexOf('沟通') > -1 || t.indexOf('申请') > -1 || t === '投递') {
                if (!b.disabled && b.offsetParent !== null) { b.click(); return t; }
            }
        }
        return '';
    """)


def run_city_keyword(page, tab, city, keyword, count, min_score, cfg, log, seen_titles):
    city_code = CITY_CODES.get(city, "")
    code_param = f"&city={city_code}" if city_code else ""

    search_url = f"https://www.liepin.com/zhaopin/?key={quote(keyword)}{code_param}"
    applied, skipped = 0, 0
    page_num = 1
    max_pages = 5

    print(f"\n{'='*50}")
    print(f"📍 {city} | 🔍 {keyword} | 🎯 上限 {count}")
    print(f"{'='*50}")

    tab.get(search_url); time.sleep(5 + random.uniform(0, 2))

    if "login" in tab.url.lower() or "passport" in tab.url.lower():
        print("⚠️  请先登录猎聘 (liepin.com)，登录后按 Enter...")
        print("⚠️  请先在投递专用Chrome中登录 liepin.com 后重新运行")
        return 0, 0
        tab.get(search_url); time.sleep(4)

    while applied < count and page_num <= max_pages:
        if page_num > 1:
            # 猎聘分页: https://www.liepin.com/zhaopin/?key=xxx&city=xxx&curPage=N
            tab.get(f"{search_url}&curPage={page_num}")
            time.sleep(4 + random.uniform(0, 2))

        tab.run_js("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)
        tab.run_js("window.scrollTo(0, 0);")
        time.sleep(1)

        cards = get_cards(tab)
        if not cards:
            page_num += 1; continue

        pending = [(i, c) for i, c in enumerate(cards) if c.get("title") and c["title"] not in seen_titles]
        if not pending:
            page_num += 1; continue

        print(f"  第{page_num}页 | {len(cards)}卡 | 待处理 {len(pending)}")

        for idx, c in pending[:min(15, max(1, count - applied))]:
            if applied >= count: break
            seen_titles.add(c.get("title", ""))

            score, reason = score_jd(c.get("title", ""), c.get("salary", ""), cfg)
            print(f"  [{score:3d}分] {c['title'][:35]} | {c['salary']} | {c['company'][:15]}")

            if score < min_score:
                skipped += 1; continue

            try:
                click_chat_button(tab, idx)
                time.sleep(2 + random.uniform(0, 1))
                applied += 1
                log["applied"].append({"job": c["title"], "salary": c["salary"],
                    "company": c["company"], "score": score, "city": city,
                    "keyword": keyword, "time": datetime.now().isoformat()})
                save_log(log, SKILL_DIR / f"liepin-{city}-log.json")
                print(f"    ✅ 已沟通 ({applied}/{count})")
            except Exception as e:
                skipped += 1
                print(f"    ❌ {e}")
            time.sleep(2 + random.uniform(0, 2))

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

    count = args.count or cfg.get("default_count", 20)
    min_score = args.min_score or cfg.get("min_score", 20)
    DAILY_LIMIT = 80  # 猎聘上限低一些，猎头为主

    print(f"""
╔══════════════════════════════════════╗
║  🤖 猎聘 自动投递 v1                 ║
╠══════════════════════════════════════╣
║  城市: {', '.join(cities[:6])}...  ║
║  搜索词: {len(keywords)} 个          ║
║  每日上限: {DAILY_LIMIT} 份          ║
╚══════════════════════════════════════╝
""")

    try:
        page = ChromiumPage(9222)
    except:
        print("❌ Chrome 未连接（端口9222）")
        return

    tab = page.new_tab("about:blank")
    total_a, total_s = 0, 0

    for city in cities:
        if total_a >= DAILY_LIMIT: break
        for keyword in keywords:
            if total_a >= DAILY_LIMIT: break
            try:
                a, s = run_city_keyword(page, tab, city, keyword, count, min_score,
                                        cfg, {"applied": [], "skipped": []}, set())
                total_a += a; total_s += s
            except Exception as e:
                print(f"  ❌ {city}/{keyword}: {e}")
            if city != cities[-1] or keyword != keywords[-1]:
                rest = 20 + random.uniform(0, 10)
                print(f"\n  ☕ 休息 {rest:.0f}s (已投 {total_a}/{DAILY_LIMIT})")
                time.sleep(rest)

    print(f"""
╔══════════════════════════════════════╗
║  猎聘全部完成                        ║
║  ✅ 沟通: {total_a}  ⏭️ 跳过: {total_s}    ║
╚══════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()

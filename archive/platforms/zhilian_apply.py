#!/usr/bin/env python3
"""智联招聘 自动投递 v1

⚠️ 首次使用前需在智联招聘完善在线简历。
⚠️ DOM 选择器需首次运行时实测校准，run_js 提取已做兜底。
"""

import argparse, json, time, random, re, sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from DrissionPage import ChromiumPage
from shared import load_config, load_log, save_log, score_jd, smart_filter

SKILL_DIR = Path(__file__).parent

# 智联城市代码 (sou.zhaopin.com 的 city 参数,四位数)
CITY_CODES = {
    "深圳": "765", "广州": "763", "北京": "530", "上海": "538",
    "东莞": "591", "佛山": "597", "惠州": "667", "珠海": "614",
    "杭州": "653", "成都": "801", "武汉": "736", "南京": "635",
    "苏州": "639", "西安": "854", "天津": "531", "重庆": "551",
}


def parse_args(cfg):
    p = argparse.ArgumentParser(description="智联招聘 自动投递")
    p.add_argument("--daily", action="store_true")
    p.add_argument("--job", default=None)
    p.add_argument("--jobs", default=None)
    p.add_argument("--city", default=None)
    p.add_argument("--cities", default=None)
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--min-score", type=int, default=None)
    return p.parse_args()


def get_cards(tab):
    """通用选择器 + sensorsdata attribute（智联也用类似埋点）"""
    cards = tab.run_js("""
        var selectors = ['.joblist-box__item', '.job-card', '.positionlist-item',
                         '[class*="joblist"] > div', '.jobList-item'];
        var items = [];
        for (var s of selectors) {
            var els = document.querySelectorAll(s);
            if (els.length > 0) {
                els.forEach(function(el) {
                    var titleEl = el.querySelector('[class*="jobTitle"], [class*="title"], [class*="name"], .jobName, .zwmc');
                    var salaryEl = el.querySelector('[class*="salary"], .salary, .zwyx');
                    var companyEl = el.querySelector('[class*="company"], [class*="comName"], .companyName, .gsmc');
                    var linkEl = el.querySelector('a[href*="job"]') || el.querySelector('a');
                    items.push({
                        el: null, // can't pass DOM
                        title: (titleEl?.textContent || '').trim(),
                        salary: (salaryEl?.textContent || '').trim(),
                        company: (companyEl?.textContent || '').trim(),
                        href: linkEl?.href || '',
                    });
                });
                if (items.length > 0) return items;
            }
        }
        return items;
    """) or []
    return cards


def click_and_apply(tab, idx):
    """点击卡片 + 在弹出的侧边栏中点击投递按钮"""
    tab.run_js(f"""
        var cards = document.querySelectorAll('[class*="joblist"] > div, .joblist-box__item, .job-card');
        if (!cards[{idx}]) return;
        cards[{idx}].scrollIntoView({{block:'center'}});
        cards[{idx}].click();
    """)
    time.sleep(2)
    # 侧边栏投递按钮
    tab.run_js("""
        var btns = document.querySelectorAll('button, a');
        for (var b of btns) {
            var t = (b.textContent||'').trim();
            if (t === '立即投递' || t === '申请职位' || t.indexOf('投递') > -1) {
                if (!b.disabled && b.offsetParent !== null) {
                    b.click(); return b.textContent;
                }
            }
        }
        return '';
    """)
    time.sleep(2)


def run_city_keyword(page, tab, city, keyword, count, min_score, cfg, log, seen_titles):
    city_code = CITY_CODES.get(city)
    if not city_code:
        print(f"  ❌ 不支持城市: {city}")
        return 0, 0

    search_url = f"https://sou.zhaopin.com/?jl={city_code}&kw={quote(keyword)}&p=1"
    applied, skipped = 0, 0
    page_num = 1
    max_pages = 6

    print(f"\n{'='*50}")
    print(f"📍 {city} | 🔍 {keyword} | 🎯 上限 {count}")
    print(f"{'='*50}")

    tab.get(search_url); time.sleep(5 + random.uniform(0, 2))

    if "login" in tab.url.lower() or "passport" in tab.url.lower():
        print("⚠️  请先登录智联招聘 (zhaopin.com)，登录后按 Enter...")
        print("⚠️  请先在投递专用Chrome中登录 zhaopin.com 后重新运行")
        return 0, 0
        tab.get(search_url); time.sleep(4)

    while applied < count and page_num <= max_pages:
        tab.get(f"https://sou.zhaopin.com/?jl={city_code}&kw={quote(keyword)}&p={page_num}")
        time.sleep(4 + random.uniform(0, 2))

        tab.run_js("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)
        tab.run_js("window.scrollTo(0, 0);")
        time.sleep(1)

        cards = get_cards(tab)
        if not cards:
            print("  无卡片，下一页"); page_num += 1; continue

        pending = [(i, c) for i, c in enumerate(cards) if c.get("title") and c["title"] not in seen_titles]
        if not pending:
            page_num += 1; continue

        print(f"  第{page_num}页 | {len(cards)}卡 | 待处理 {len(pending)}")

        for idx, c in pending[:max(15, count - applied)]:
            if applied >= count: break
            seen_titles.add(c.get("title", ""))

            score, reason = score_jd(c.get("title", ""), c.get("salary", ""), cfg)
            print(f"  [{score:3d}分] {c['title'][:35]} | {c['salary']} | {c['company'][:15]}")

            if score < min_score:
                skipped += 1; continue

            try:
                click_and_apply(tab, idx)
                time.sleep(2 + random.uniform(0, 1))
                applied += 1
                log["applied"].append({"job": c["title"], "salary": c["salary"],
                    "company": c["company"], "score": score, "city": city,
                    "keyword": keyword, "time": datetime.now().isoformat()})
                save_log(log, SKILL_DIR / f"zhilian-{city}-log.json")
                print(f"    ✅ 已投递 ({applied}/{count})")
            except Exception as e:
                skipped += 1
                print(f"    ❌ {e}")

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
║  🤖 智联招聘 自动投递 v1              ║
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
║  智联全部完成                        ║
║  ✅ 投递: {total_a}  ⏭️ 跳过: {total_s}    ║
╚══════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()

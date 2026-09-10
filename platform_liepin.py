#!/usr/bin/env python3
"""猎聘 自动投递 v4 — 升级: 9223 + evaluate_job(L2决策) + record_application落库 + 日限50

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

CITY_CODES = {
    "深圳": "050090000", "广州": "050020000", "北京": "010000000", "上海": "020000000",
    "东莞": "050180000", "佛山": "050040000", "杭州": "070020000", "成都": "280020000",
    "武汉": "170020000", "南京": "060020000", "苏州": "060100000", "西安": "110100000",
}
DAILY_LIMIT = 50
PORT = 9223


def get_cards(tab):
    """猎聘卡片：class 混淆名,按文本结构抓(标题在卡片首行大字号,薪资含 k/万)"""
    return tab.run_js("""
        var cards = document.querySelectorAll('.job-detail-box');
        if (cards.length < 3) cards = document.querySelectorAll('[class*="job-list"] > div');
        var out = [];
        for (var el of cards) {
            var txt = (el.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!txt || txt.length < 10 || txt.length > 250) continue;
            // 标题 = 第一段(通常含中文+字母, <40字)
            var lines = el.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
            var title = lines[0] || '';
            if (title.length > 45) title = title.slice(0, 45);
            // 薪资 = 含 k/K/万/千 的行
            var salary = '';
            for (var l of lines) {
                if (/[0-9][kK万千]|年薪|万\\/年/.test(l)) { salary = l.slice(0, 20); break; }
            }
            // 公司 = 含 公司/集团/有限/科技 或 (深圳) 后缀 的行(排除标题行)
            var company = '';
            for (var l of lines) {
                if (l === title) continue;
                if (l.length > 3 && l.length < 40 && /公司|集团|有限|股份|科技|咨询|贸易|实业|投资|人力/.test(l)) { company = l; break; }
            }
            // 兜底: 公司常跟"行业 规模 融资"一起, 取第一个超过6字的非薪资非年限行
            if (!company) {
                for (var l of lines) {
                    if (l === title || /[0-9]/.test(l.slice(0,2))) continue;
                    if (l.length > 6 && l.length < 45) { company = l; break; }
                }
            }
            var a = el.querySelector('a[href*="job"]') || el.querySelector('a');
            out.push({
                jobId: (a?.href || '').replace(/\\D/g, '').slice(0, 12) || title.slice(0, 20),
                title: title,
                salary: salary,
                company: company,
                href: a?.href || '',
                btnText: '',
            });
        }
        return out;
    """) or []



def click_apply_and_check(tab, idx, cards_sel):
    """猎聘：点卡片 <a> 导航到岗位详情页 → 详情页点 立即沟通/投递"""
    # 1. 卡片是一个 <a href=job/xxx.shtml>，直接取 href 导航
    href = tab.run_js(f"""
        var cards = document.querySelectorAll('{cards_sel}');
        if (!cards[{idx}]) return '';
        var a = cards[{idx}].querySelector('a[href*="job"]') || cards[{idx}].querySelector('a');
        return a ? a.href : '';
    """)
    if not href:
        return 'NO_HREF'
    tab.get(href)
    time.sleep(3 + random.uniform(0, 1))
    # 2. 详情页找 立即沟通/投递 按钮
    state = tab.run_js("""
        var btns = document.querySelectorAll('button, a, div[class*="btn"]');
        var targets = ['立即沟通', '立即投递', '投递简历', '投递', '申请职位', '沟通'];
        for (var b of btns) {
            var txt = (b.innerText || '').trim().replace(/\\s+/g, '');
            for (var tg of targets) {
                if (txt.indexOf(tg) > -1 && !b.disabled && b.offsetParent !== null) {
                    b.click();
                    return txt;
                }
            }
        }
        // 兜底：看页面是否已显示"已投递/已沟通"
        var body = document.body.innerText;
        if (body.indexOf('已投递') > -1 || body.indexOf('已沟通') > -1) return '已投递/已沟通';
        return 'NO_BTN:' + body.slice(0, 80);
    """)
    time.sleep(2)
    return state



def run_city_keyword(page, tab, city, keyword, count, seen, today_applied):
    city_code = CITY_CODES.get(city)
    if not city_code:
        return 0, 0
    url = f"https://www.liepin.com/zhaopin/?key={quote(keyword)}&city={city_code}"
    applied, skipped = 0, 0
    page_num = 1
    empty_streak = 0

    print(f"\n{'='*50}\n📍 {city} | 🔍 {keyword} | 🎯 {count}\n{'='*50}")
    tab.get(url); time.sleep(4 + random.uniform(0, 2))

    if "login" in tab.url.lower():
        print("⚠️ 未登录 猎聘"); return 0, 0

    while applied < count and empty_streak < 3 and page_num <= 6:
        if page_num > 1:
            tab.get(f"{url}&pageNum={page_num}"); time.sleep(3 + random.uniform(0, 2))
        tab.run_js("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(1.5)
        tab.run_js("window.scrollTo(0, 0);"); time.sleep(1)

        cards = get_cards(tab)
        if not cards:
            empty_streak += 1; page_num += 1; continue

        sel_used = '.job-list-item' if tab.run_js("return !!document.querySelector('.job-list-item')") else '[class*="job-list"] > div'
        pending = [c for c in cards if c["jobId"] not in seen and "已申请" not in c["btnText"] and "已投递" not in c["btnText"]]
        if not pending:
            page_num += 1; empty_streak += 1; continue
        empty_streak = 0
        print(f"  第{page_num}页 | {len(cards)}卡 | 待处理 {len(pending)}")

        for idx, c in enumerate(pending):
            if applied >= count or today_applied >= DAILY_LIMIT:
                break
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
            state = click_apply_and_check(tab, idx, sel_used)
            if "已申请" in state or "已投递" in state:
                applied += 1
                today_applied += 1
                try:
                    record_application(
                        platform="liepin", city=city, company=c["company"] or "未知",
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

    print(f"╔══ 猎聘 v4 ══ 城市{len(cities)} 词{len(keywords)} 上限{DAILY_LIMIT}/天 ══╗")
    try:
        page = ChromiumPage(PORT)
    except Exception as e:
        print(f"❌ Chrome 未连接(端口{PORT}): {e}")
        return
    tab = page.new_tab("about:blank")
    seen = set()
    today_applied = 0
    total_a = total_s = 0
    try:
        for city in cities:
            if today_applied >= DAILY_LIMIT: break
            for kw in keywords:
                if today_applied >= DAILY_LIMIT: break
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
    print(f"╔══ 猎聘完成 ══ ✅ {total_a} 投 | ⏭️ {total_s} 跳 ══╗")


if __name__ == "__main__":
    main()

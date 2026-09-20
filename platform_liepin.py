#!/usr/bin/env python3
"""猎聘 自动投递 v4 — 升级: 9223 + evaluate_job(L2决策) + record_application落库 + 日限50

复用 v3 的抓取/点击核心(sensorsdata结构化卡片), 决策层从 score_jd 换成 job_decision.evaluate_job
规则全平台统一: 底薪≥8K / 薪资≤60K / 排除销售标注狼性实习
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
from notify import alert

# 猎聘 dqs 城市码(2026-09-10 实测: URL 用 &dqs= 而非 &city=, 否则返回全国异地岗)
CITY_CODES = {
    "深圳": "050090", "广州": "050020", "北京": "010000", "上海": "020000",
    "东莞": "050180", "佛山": "050040", "杭州": "070020", "成都": "280020",
    "武汉": "170020", "南京": "060020", "苏州": "060100", "西安": "110100",
}
DAILY_LIMIT = 20  # 2026-09-11: 猎聘风控敏感(详情页有强限速),宁少勿封
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



def click_apply_and_check(tab, href, page=None):
    """猎聘：新 tab 打开岗位详情页 → 点 投递简历

    2026-09-10: 同一 tab 从搜索页连续 get 详情页会被猎聘识别成自动化(返回空页),
    改用新 tab 打开(模拟真实"新开页签看岗位"行为),用完关闭。
    """
    if not href:
        return 'NO_HREF'
    clean = href.split('?')[0]
    # 2026-09-10: tab.get() 直接导航被猎聘识别成自动化(返回空页)。
    # 改为模拟真实用户"点击卡片链接", 让 SPA 自己路由到详情页。
    # 2026-09-11 实测: 猎聘详情页有强限速——间隔 <60s 会被重置回列表页(not_detail),
    # 间隔 >=65s 才能正常打开并读到"投简历"按钮。故此处必须慢。
    wait = 60 + random.uniform(0, 15)
    print(f"    ⏳ 猎聘限速等待 {wait:.0f}s ...")
    time.sleep(wait)
    detail_tab = page.new_tab(clean) if page is not None else None
    if detail_tab is not None:
        work = detail_tab
    else:
        tab.get(clean)
        work = tab
    try:
        # 2026-09-11: 猎聘详情页加载慢且不稳定(有时 title 还只显示"猎聘"就返回),
        # 改为轮询等待: 每 2s 检查页面是否出现岗位内容, 最多等 25s。
        loaded = False
        for _ in range(13):
            time.sleep(2)
            try:
                ok = work.run_js(
                    "return /职位描述|岗位职责|任职要求|工作职责|岗位要求|职位信息|工作内容/.test(document.body.innerText)")
            except Exception:
                ok = False
            if ok:
                loaded = True
                break
        if not loaded:
            time.sleep(3)
        work.run_js("window.scrollTo(0, document.body.scrollHeight * 0.4);")
        time.sleep(1.5)
        state = _find_and_click(work)
    finally:
        if detail_tab is not None:
            try:
                detail_tab.close()
            except Exception:
                pass
    return state


def _find_and_click(work):
    """在详情页找投递按钮点击。猎聘真按钮文案='投简历'(A.btn-minor)"""
    state = work.run_js("""
        var sels = 'button, a, div[class*="btn"], span[class*="btn"]';
        var targets = ['投简历', '投递简历', '立即投递', '申请职位', '立即沟通'];
        var btns = document.querySelectorAll(sels);
        for (var b of btns) {
            var txt = (b.innerText || '').trim().replace(/\\s+/g, '');
            for (var tg of targets) {
                if (txt.indexOf(tg) > -1 && !b.disabled && b.offsetParent !== null) {
                    b.click();
                    return 'CLICKED:' + txt;
                }
            }
        }
        var body = document.body.innerText;
        if (body.indexOf('已投递') > -1 || body.indexOf('已沟通') > -1) return 'ALREADY';
        var isDetail = /职位描述|岗位职责|任职要求|工作职责|岗位要求|职位信息|工作内容|薪资|经验要求/.test(body);
        return 'NO_BTN:' + (isDetail ? 'detail_page' : 'not_detail') + '|' + document.title.slice(0, 40);
    """)
    time.sleep(4)
    return state



def run_city_keyword(page, tab, city, keyword, count, seen, today_applied):
    city_code = CITY_CODES.get(city)
    if not city_code:
        return 0, 0
    url = f"https://www.liepin.com/zhaopin/?key={quote(keyword)}&dqs={city_code}"
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
        # 2026-09-11: 跳过 /a/ 开头的猎头岗(无"投简历"按钮, 且猎头只接中高端岗,
        # 对初级求职者价值低); 只投 /job/ 开头的公司直招岗(有"投简历"按钮)。
        pending = [c for c in cards if c["jobId"] not in seen
                   and "已申请" not in c["btnText"] and "已投递" not in c["btnText"]
                   and "/job/" in (c.get("href") or "")]
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
            state = click_apply_and_check(tab, c.get('href', ''), page)
            # 2026-09-19 修正：猎聘走的是 _find_and_click，成功回执是 'CLICKED:投简历'，
            # 而 51job 的 click_apply_and_check 才返回「已申请/已投递」。
            # 原来这里只认后者 → 每一次真实投递都落进 else 失败分支（猎聘长期 0 入库的第一个因）。
            if "已申请" in state or "已投递" in state or state.startswith("CLICKED"):
                applied += 1
                today_applied += 1
                try:
                    record_application(
                        platform="liepin", city=city, company=c["company"] or "未知",
                        title=c["title"], salary=c["salary"], keyword=keyword,
                        score=0, resume_version="E", decision="ALLOW",
                        # 2026-09-15 修正：与 51job 同一处 bug —— 这是「按钮回执已确认」
                        # 的成功分支，原来却写死 UNCERTAIN/verified=0。
                        status="APPLIED", reason=str(reason)[:60],
                        verified=1, event_type="apply", event_error=None,
                        extra_payload={"jobId": c["jobId"], "area": c.get("area", ""),
                                       "button_state": state,
                                       "evidence": "liepin按钮回执"},
                        gates=None, greeting_template_id=None,
                    )
                except Exception as e:
                    print(f"    ⚠️ 落库失败: {e}")
                print(f"    ✅ 已投递 ({applied}/{count}, 今日{today_applied}/{DAILY_LIMIT})")
            else:
                skipped += 1
                # 2026-09-15：与 51job 一致，失败也落库（此前只 print，库里看不见）
                try:
                    record_application(
                        platform="liepin", city=city, company=c["company"] or "未知",
                        title=c["title"], salary=c["salary"], keyword=keyword,
                        score=0, resume_version="E", decision="failed",
                        status="FAILED", reason=f"按钮未确认:{state or '无回执'}"[:80],
                        verified=0, event_type="apply",
                        event_error=f"按钮状态未确认: {state or '空回执'}",
                        extra_payload={"jobId": c["jobId"], "area": c.get("area", "")},
                        gates=None, greeting_template_id=None,
                    )
                except Exception as e:
                    print(f"    ⚠️ 落库失败: {e}")
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

    # 2026-09-15：与 shared.py 的规矩对齐 —— 写操作前必须查急停开关
    from shared import kill_switch_check
    import reply_lock     # 回复审核锁（v5 第十二条）
    _allowed, _kreason = kill_switch_check()
    if not _allowed:
        print(f"⛔ kill switch 生效中，本轮猎聘不投递：{_kreason}")
        alert("kill_switch_block", "kill switch 生效中，猎聘本轮未投递",
              f"原因：{_kreason}\n恢复：python3 boss_apply.py --kill-off",
              level="warn", throttle=1800)
        return
    # ── 全局 Chrome 互斥（2026-09-19）──
    from chrome_lock import acquire as _chrome_acquire
    if not _chrome_acquire("liepin", wait_seconds=1500, max_minutes=45):
        return
    try:
        page = ChromiumPage(PORT)
    except Exception as e:
        print(f"❌ Chrome 未连接(端口{PORT}): {e}")
        alert("chrome_down", "猎聘无法连接 Chrome，本轮没投出去",
              f"端口 {PORT} 连不上：{e}", level="error", throttle=1800)
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
                # ── 回复审核锁（v5 第十二条：回复 > 投递）——2026-09-18 补，此前只有 Boss 查 ──
                if reply_lock.is_locked():
                    print(f"\n  🔒 [REPLY_REVIEW_LOCK] 检测到待审核 HR 回复 — "
                          f"猎聘在 {city}×{kw} 前暂停本轮投递")
                    alert("reply_lock_block", f"猎聘因待审 HR 回复暂停（{city}×{kw}）",
                          "有待审核的 HR 回复时暂停自动投递（v5 第十二条，设计行为）。\n"
                          "审核：python3 reply_lock.py review ；审完自动恢复。",
                          level="info", throttle=3600)
                    return
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

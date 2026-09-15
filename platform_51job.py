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
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from DrissionPage import ChromiumPage
from job_decision import evaluate_job
from store import record_application
from notify import alert


def in_night_window() -> bool:
    """夜间禁投 22:00-8:00（与 boss_apply 同规则，封号红线）"""
    h = datetime.now().hour
    return h >= 22 or h < 8


# ── tab 健康检查与自动重建（2026-09-12 修复）──
# 事故：本轮投到第 100 条时 tab 掉线（"The connection to the page has been
# disconnected"），此后每个关键词都在 tab.get() 处抛异常，剩下 6 个关键词全部
# 空转作废。Boss 侧有 _pick_boss_tab 兜底，51job 之前完全没有。
TAB_READY_WAIT = 2        # new_tab 后等待就绪秒数（测试可置 0）
TAB_RETRY_WAIT = 3        # 重建重试间隔秒数（测试可置 0）
HOURLY_REST_SEC = 30 * 60  # 单小时熔断休息时长，与 boss_apply 一致（测试可置 0）


def _tab_alive(tab) -> bool:
    """tab 是否还活着（能执行 JS 即视为活着）。"""
    if tab is None:
        return False
    try:
        tab.run_js("return 1;")
        return True
    except Exception:
        return False


def ensure_tab(page, tab):
    """tab 失联则重建（关旧 + 开新），最多重试 3 次。

    返回可用 tab；重建失败返回 None（调用方应中止本轮，不要继续空转）。
    """
    if _tab_alive(tab):
        return tab
    print("  🔧 投递 tab 失联，重建中…")
    if tab is not None:
        try:
            tab.close()
        except Exception:
            pass
    for i in range(1, 4):
        try:
            new_tab = page.new_tab("about:blank")
            time.sleep(TAB_READY_WAIT)
            if _tab_alive(new_tab):
                print("  ✅ tab 已重建")
                return new_tab
        except Exception as e:
            print(f"  ⚠️ tab 重建失败({i}/3): {e}")
        time.sleep(TAB_RETRY_WAIT)
    print("  🛑 tab 连续 3 次重建失败，本轮收工")
    return None


# ── 单小时熔断（2026-09-12 补：51job 原先只认日限额，小时闸完全没接）──
# 与 boss_apply 同约定：本小时已投 ≥ cap → 休息 30 分钟再继续（分段 sleep 可中断）。
# 依据：2026-08-11 封号事故（单小时 47 份是主因之一）。
def hourly_applied(hour_prefix: Optional[str] = None) -> int:
    """本小时已投条数（跨进程，查 DB）。

    ⚠️ created_at 是 ISO 格式（'2026-09-12T08:14:33'，T 分隔），不是空格分隔。
    2026-09-12 踩坑：首版用 "%Y-%m-%d %H" 生成前缀，与 DB 永远匹配不上 → 恒返回 0
    → 小时闸形同虚设。必须用 "%Y-%m-%dT%H"。
    """
    import sqlite3
    hour_prefix = hour_prefix or datetime.now().strftime("%Y-%m-%dT%H")
    try:
        con = sqlite3.connect(str(Path(__file__).parent / 'ab_experiment.db'))
        n = con.execute(
            "SELECT COUNT(*) FROM applications_v2 WHERE platform='51job' "
            "AND substr(created_at,1,13)=? AND status IN ('UNCERTAIN','APPLIED','VERIFIED')",
            (hour_prefix,)).fetchone()[0]
        con.close()
        return n or 0
    except Exception:
        return 0


def hourly_gate(cap: int) -> None:
    """本小时达上限 → 休息 30 分钟（与 boss_apply 同款分段 sleep）。"""
    if not cap:
        return
    n = hourly_applied()
    if n < cap:
        return
    rest = HOURLY_REST_SEC
    print(f"\n  🕐 [hour_limit_reached] 本小时已投 {n} 条 ≥ {cap}，"
          f"暂停 {rest // 60} 分钟防风控（kill switch 未动）")
    for _ in range(max(0, rest) // 5):
        time.sleep(5)
    print(f"  ▶️ 休息结束，继续（本小时 {hourly_applied()}/{cap}）")

CITY_CODES = {
    "深圳": "040000", "广州": "030200", "北京": "010000", "上海": "020000",
    "东莞": "030800", "佛山": "030600", "惠州": "031600", "珠海": "030400",
    "杭州": "080200", "成都": "090200", "武汉": "170200", "南京": "060200",
    "苏州": "060800", "西安": "110200", "天津": "030500", "重庆": "040200",
}
DAILY_LIMIT = 100  # 2026-09-12: 用户定稿「以 9/12 实际投出量 100 作为该平台日限额」（原 120）
HOURLY_CAP = 10    # 单小时上限（启动时从 config.json 的 safety 块覆盖；2026-09-12 接入）
PORT = 9223


def load_safety() -> dict:
    """读取 config.json 的 safety 块（与 boss_apply.get_safety 同源，缺省保守）。

    注：原来 main() 里那句 `import config` 是死代码——仓库只有 config/ 目录，
    被 Python 当命名空间包导入，拿不到任何配置。
    """
    defaults = {"hourly_cap": 10, "night_ban_start": 22, "night_ban_end": 8}
    try:
        from shared import load_config
        cfg = load_config()
        defaults.update(cfg.get("safety") or {})
    except Exception as e:
        print(f"⚠️ 读取 safety 配置失败({e})，用保守默认值 {defaults}")
    return defaults



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
    """返回 (applied, skipped, tab)。

    ⚠️ tab 可能在中途被重建，调用方必须接住返回的新引用（2026-09-12 修复）。
    """
    city_code = CITY_CODES.get(city)
    if not city_code:
        return 0, 0, tab
    url = f"https://we.51job.com/pc/search?keyword={quote(keyword)}&jobArea={city_code}&degree=04&workyear=02,03"
    applied, skipped = 0, 0
    page_num = 1
    empty_streak = 0

    print(f"\n{'='*50}\n📍 {city} | 🔍 {keyword} | 🎯 {count}\n{'='*50}")

    def goto(tab_, pnum):
        """导航到第 pnum 页；tab 失联则重建重试。返回 (tab, ok)。"""
        target = url if pnum == 1 else f"{url}&pageNum={pnum}"
        for attempt in range(1, 4):
            tab_ = ensure_tab(page, tab_)
            if tab_ is None:
                return None, False
            try:
                tab_.get(target)
            except Exception as e:
                print(f"  ⚠️ 导航失败({attempt}/3): {e}")
                time.sleep(3)
                continue
            time.sleep(4 + random.uniform(0, 2))
            if _tab_alive(tab_):
                return tab_, True
            print(f"  ⚠️ 导航后 tab 失联，重建重试({attempt}/3)")
        return tab_, False

    tab, ok = goto(tab, 1)
    if not ok or tab is None:
        print("  🛑 首屏导航失败，跳过本关键词")
        return applied, skipped, tab

    if "login" in (tab.url or "").lower():
        print("⚠️ 未登录 51job")
        alert("login", "51job 未登录，本轮投递作废",
              "浏览器里的 51job 登录态掉了，需要手动登录后重跑。\n"
              "在本轮修复前，这种情况是静默的。", level="error", throttle=0)
        return applied, skipped, tab

    while applied < count and empty_streak < 3 and page_num <= 6:
        tab, ok = goto(tab, page_num)
        if not ok or tab is None:
            print("  🛑 导航失败，结束本关键词")
            break
        tab.run_js("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(1.5)
        tab.run_js("window.scrollTo(0, 0);"); time.sleep(1)

        try:
            cards = get_cards(tab)
        except Exception as e:
            print(f"  ⚠️ 抓卡片失败: {e}")
            tab = ensure_tab(page, tab)
            if tab is None:
                break
            continue
        if not cards:
            empty_streak += 1; page_num += 1; continue

        pending = [c for c in cards if c["jobId"] not in seen and "已申请" not in c["btnText"] and "已投递" not in c["btnText"]]
        if not pending:
            page_num += 1; empty_streak += 1; continue
        empty_streak = 0
        print(f"  第{page_num}页 | {len(cards)}卡 | 待处理 {len(pending)}")

        tab_lost = False
        for c in pending:
            if applied >= count or today_applied >= DAILY_LIMIT:
                break
            if in_night_window():
                print("  🌙 夜间禁投，停止本页处理")
                return applied, skipped, tab
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

            # tab 掉线保护：点击前先确认 tab 活着（2026-09-12 修复）
            if not _tab_alive(tab):
                print("  ⚠️ tab 失联（点击前），重建并重新抓卡片")
                tab = ensure_tab(page, tab)
                if tab is None:
                    return applied, skipped, tab
                tab_lost = True
                break
            # 单小时熔断（与 boss_apply 同约定：满了休息 30 分钟再继续）
            hourly_gate(HOURLY_CAP)

            print(f"  [✅{c['title'][:30]}] | {c['salary']} | {c['company'][:15]}")
            try:
                state = click_apply_and_check(tab, c["jobId"])
            except Exception as e:
                print(f"    ⚠️ 点击异常: {e}")
                state = ""
            if "已申请" in state or "已投递" in state:
                # 2026-09-15 修正：这里原来硬编码 status="UNCERTAIN"、verified=0，
                # 但它其实是**成功分支** —— 按钮回执已经变成「已申请/已投递」，
                # 且上一行已经 applied += 1 计数。后果：384 条真实成功的投递在库里
                # 全是「不确定」，51job 在复盘里永远是「从未验证」；而真正投失败的
                # 那条路（下面的 else）只 print 不落库 —— 两个方向刚好记反了。
                # 现在按 store 的约定写 applied/APPLIED + verified=1，并把按钮回执
                # 原文留作证据；decision 仍写 ALLOW（L2 判决），闸门计数口径不变。
                applied += 1
                today_applied += 1
                try:
                    record_application(
                        platform="51job", city=city, company=c["company"] or "未知",
                        title=c["title"], salary=c["salary"], keyword=keyword,
                        score=0, resume_version="E", decision="ALLOW",
                        status="APPLIED", reason=str(reason)[:60],
                        verified=1, event_type="apply", event_error=None,
                        extra_payload={"jobId": c["jobId"], "area": c["area"],
                                       "button_state": state,
                                       "evidence": "51job按钮回执"},
                        gates=None, greeting_template_id=None,
                    )
                except Exception as e:
                    print(f"    ⚠️ 落库失败: {e}")
                print(f"    ✅ 已投递 ({applied}/{count}, 今日{today_applied}/{DAILY_LIMIT})")
            elif not _tab_alive(tab):
                # 点击过程中掉线：本次结果不可确定 → 不计入，重建后回外层重来
                print("    ⚠️ 点击后 tab 失联，本次结果不确定（不计入）")
                tab = ensure_tab(page, tab)
                if tab is None:
                    return applied, skipped, tab
                tab_lost = True
                break
            else:
                # 2026-09-15 新增：点击后按钮回执没变成「已申请/已投递」（已重试 2 次）
                # → 记一条 FAILED。以前这条路只 print 不落库，所以「投失败的」在库里
                # 完全看不见，只能看到一堆 UNCERTAIN。
                # decision 写 "failed" —— 它不在闸门计数三态（applied/uncertain/ALLOW）
                # 里，所以不占日/小时额度、也不会被同公司去重拦住，明天可以重投。
                skipped += 1
                try:
                    record_application(
                        platform="51job", city=city, company=c["company"] or "未知",
                        title=c["title"], salary=c["salary"], keyword=keyword,
                        score=0, resume_version="E", decision="failed",
                        status="FAILED", reason=f"按钮未确认:{state or '无回执'}"[:80],
                        verified=0, event_type="apply",
                        event_error=f"按钮状态未确认: {state or '空回执'}",
                        extra_payload={"jobId": c["jobId"], "area": c["area"]},
                        gates=None, greeting_template_id=None,
                    )
                except Exception as e:
                    print(f"    ⚠️ 落库失败: {e}")
                print(f"    ❌ 按钮状态: {state}")
            time.sleep(2 + random.uniform(0, 2))
        if tab_lost:
            continue          # DOM 已重置：回外层重新导航+抓卡（seen 已去重，不会重复处理）
        page_num += 1
    return applied, skipped, tab


def main():
    global HOURLY_CAP
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

    _safety = load_safety()
    HOURLY_CAP = int(_safety.get("hourly_cap", HOURLY_CAP) or 0)

    print(f"╔══ 51job v4 ══ 城市{len(cities)} 词{len(keywords)} "
          f"上限{DAILY_LIMIT}/天 {HOURLY_CAP}/时 ══╗")
    try:
        page = ChromiumPage(PORT)
    except Exception as e:
        print(f"❌ Chrome 未连接(端口{PORT}): {e}")
        alert("chrome_down", "51job 无法连接 Chrome，本轮没投出去",
              f"端口 {PORT} 连不上：{e}\n先确认调试端口的 Chrome 起着。",
              level="error", throttle=1800)
        return
    tab = page.new_tab("about:blank")
    tab = ensure_tab(page, tab)
    if tab is None:
        print("❌ 无法获得可用投递 tab，退出")
        return
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
            alert("quota_full", f"51job 今日额度已满（{today_applied}/{DAILY_LIMIT}）",
                  "这是正常收工，不用处理。", level="info", throttle=43200)
            return
    except Exception as e:
        print(f"⚠️ DB统计失败({e})，按0计")
    total_a = total_s = 0
    fatal = False
    try:
        for city in cities:
            if today_applied >= DAILY_LIMIT or in_night_window() or fatal: break
            for kw in keywords:
                if today_applied >= DAILY_LIMIT: break
                if in_night_window():
                    print("  🌙 已到夜间禁投时段(22:00-8:00)，本轮收工")
                    break
                # 每个关键词前确认 tab 可用（掉线就重建，别让剩余关键词空转作废）
                tab = ensure_tab(page, tab)
                if tab is None:
                    print("  🛑 tab 无法恢复，本轮收工")
                    alert("tab_lost", "51job 页面 tab 反复重建失败，本轮提前收工",
                          "连续 3 次无法恢复页面，剩下的城市/关键词全部空转作废。",
                          level="warn", throttle=0)
                    fatal = True
                    break
                try:
                    a, s, tab = run_city_keyword(page, tab, city, kw, count, seen, today_applied)
                    total_a += a; total_s += s; today_applied += a
                except Exception as e:
                    print(f"  ❌ {city}/{kw}: {e}")
                if today_applied < DAILY_LIMIT:
                    rest = 15 + random.uniform(0, 10)
                    print(f"  ☕ 休息 {rest:.0f}s (今日 {today_applied}/{DAILY_LIMIT})")
                    time.sleep(rest)
    finally:
        try:
            if tab is not None:
                tab.close()
        except Exception:
            pass
    print(f"╔══ 51job完成 ══ ✅ {total_a} 投 | ⏭️ {total_s} 跳 ══╗")


if __name__ == "__main__":
    main()

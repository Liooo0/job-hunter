#!/usr/bin/env python3
"""Boss直聘自动投递脚本 v2 — 多城市 + 多关键词 + 自动报告"""

import argparse
import json
import os
import random
import signal
import sys
import time
from datetime import datetime, date
from pathlib import Path

from DrissionPage import ChromiumPage, ChromiumOptions
from typing import Optional

from shared import load_config, load_log, save_log, score_jd, smart_filter, get_chrome_opts
from deep_filter import deep_filter, run_company_background_check
from report import print_terminal_summary, generate_html

# ═══════════════════════════════════════════════════════════════
#  暂停机制：关终端/关浏览器 = 暂停，写 .paused 文件防 launchd 重拉
#  睡眠模式：连续登录失败 N 次 → 自动暂停，等待手动恢复
# ═══════════════════════════════════════════════════════════════

SHOULD_STOP = False
STOP_REASON = ""
INTERACTIVE = sys.stdin.isatty()  # 终端里手动跑=True, launchd定时=False
SKILL_DIR = Path(__file__).parent
PAUSE_FILE = SKILL_DIR / ".paused"
SLEEP_TRACKER = SKILL_DIR / ".sleep_tracker"
MAX_LOGIN_FAILS = 3  # 连续3次登录失败 → 进入睡眠模式


def _signal_handler(signum, frame):
    global SHOULD_STOP, STOP_REASON
    names = {signal.SIGHUP: "终端关闭(SIGHUP)", signal.SIGTERM: "SIGTERM",
             signal.SIGINT: "Ctrl+C(SIGINT)"}
    SHOULD_STOP = True
    STOP_REASON = names.get(signum, f"信号{signum}")
    pause(STOP_REASON)
    print(f"\n⏸️  收到 {STOP_REASON}，正在优雅停止...")


signal.signal(signal.SIGHUP, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ── 暂停锁文件 (.paused) ──

def is_paused() -> bool:
    """检查 .paused 文件是否存在（即用户之前关终端/关浏览器触发的暂停）。"""
    return PAUSE_FILE.exists()


def pause(reason: str):
    """写入暂停锁文件，防止 launchd 定时任务继续触发。"""
    PAUSE_FILE.write_text(
        json.dumps({"paused_at": datetime.now().isoformat(), "reason": reason},
                   ensure_ascii=False)
    )
    print(f"\n📌 已写入暂停锁 .paused — launchd 定时任务将跳过，直到你手动清除")
    print(f"   恢复命令: python3 boss_apply.py --resume")


def resume():
    """删除暂停锁和睡眠追踪，恢复正常投递。"""
    cleared = []
    if PAUSE_FILE.exists():
        PAUSE_FILE.unlink()
        cleared.append("暂停锁 (.paused)")
    if SLEEP_TRACKER.exists():
        SLEEP_TRACKER.unlink()
        cleared.append("睡眠追踪 (.sleep_tracker)")
    if cleared:
        print(f"✅ 已清除: {', '.join(cleared)} — 恢复正常投递")
    else:
        print(f"ℹ️  当前未暂停")


# ── 睡眠追踪：连续登录失败自动暂停 ──

def _load_sleep_tracker() -> dict:
    if SLEEP_TRACKER.exists():
        try:
            return json.loads(SLEEP_TRACKER.read_text())
        except Exception:
            pass
    return {"fail_count": 0, "last_fail": None}


def _save_sleep_tracker(data: dict):
    SLEEP_TRACKER.write_text(json.dumps(data, ensure_ascii=False))


def record_login_ok():
    """登录成功 → 重置失败计数。"""
    if SLEEP_TRACKER.exists():
        SLEEP_TRACKER.unlink()
        print("  ✅ 登录正常，重置失败计数")


def record_login_fail() -> bool:
    """登录失败 +1，返回 True 表示已触发睡眠模式。"""
    data = _load_sleep_tracker()
    data["fail_count"] = data.get("fail_count", 0) + 1
    data["last_fail"] = datetime.now().isoformat()
    _save_sleep_tracker(data)

    if data["fail_count"] >= MAX_LOGIN_FAILS:
        pause(f"连续{MAX_LOGIN_FAILS}次登录失败，进入睡眠模式")
        return True
    print(f"  ⚠️  登录失败 {data['fail_count']}/{MAX_LOGIN_FAILS}（连续{MAX_LOGIN_FAILS}次将进入睡眠）")
    return False


def get_sleep_status() -> Optional[str]:
    """返回睡眠状态描述，未睡眠返回 None。"""
    data = _load_sleep_tracker()
    if data.get("fail_count", 0) > 0:
        last = data.get("last_fail", "?")
        return f"登录失败 {data['fail_count']}/{MAX_LOGIN_FAILS} 次 (最近: {last})"
    return None


def _parent_alive() -> bool:
    """终端关了 → 父进程变成 launchd(pid=1)，检测到这个就返回 False"""
    ppid = os.getppid()
    if ppid == 1:
        return False
    try:
        import psutil
        try:
            parent = psutil.Process(ppid)
            parent_name = parent.name() or ""
            if parent_name in ("launchd", "init", "systemd"):
                return False
        except Exception:
            return True  # 能读到进程且不是 init，算活着
    except ImportError:
        pass
    return True


def check_should_stop(page=None) -> bool:
    """检查是否应该暂停。

    触发条件:
      - 收到 SIGHUP/SIGTERM/SIGINT
      - 终端中运行 && 终端已关闭 (父进程变成 launchd)
      - Chrome 已关闭 (page ping 失败)
    """
    global SHOULD_STOP, STOP_REASON
    if SHOULD_STOP:
        return True

    # 终端运行时才检查终端是否还活着（launchd 定时任务不管终端）
    if INTERACTIVE:
        if not _parent_alive():
            SHOULD_STOP = True
            STOP_REASON = "终端窗口已关闭"
            pause(STOP_REASON)
            print(f"\n⏸️  {STOP_REASON}，正在优雅停止...")
            return True

    # Chrome 存活检测
    if page is not None:
        try:
            page.run_js("1")
        except Exception:
            SHOULD_STOP = True
            STOP_REASON = "Chrome浏览器已关闭"
            pause(STOP_REASON)
            print(f"\n⏸️  {STOP_REASON}，正在优雅停止...")
            return True

    return False


def _safe_input_or_skip(prompt: str, timeout: int = 60):
    """非交互模式直接返回 None(跳过)；交互模式等用户输入，但也会检查终端/Chrome。

    返回 None 表示跳过，返回字符串表示用户输入。
    """
    if not INTERACTIVE:
        print(f"⚠️  {prompt}")
        print("   非交互模式(launchd定时任务)，自动跳过")
        return None
    try:
        import select
        print(prompt, end="", flush=True)
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if r:
            return sys.stdin.readline().rstrip("\n")
        else:
            print(f"\n  超时({timeout}s)无输入，跳过")
            return None
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════
#  智能招呼语生成器 — 根据 JD 内容自动生成个性化打招呼
# ═══════════════════════════════════════════════════════════════

# 用户背景素材库（JD匹配到哪个方向就用对应的经历）
USER_BG = {
    "AI应用": "我独立搭建过完整的AI应用系统，比如多平台数据自动化采集与智能筛选的管线，从浏览器操控到AI评分引擎全链路自己搞定",
    "Agent": "我深挖过Agent编排和MCP协议，用Dify和Coze搭过工作流，能独立交付从需求到上线的智能体方案",
    "RPA": "我用Python全自研了多平台自动化操控系统，对RPA的思路很熟悉，影刀也跑过完整流程",
    "自动化": "我擅长用Python+Shell做自动化，之前写的多平台数据采集脚本日处理千级数据，替代了人工筛选",
    "低代码": "我在Dify和Coze上搭过完整的业务工作流，能快速把想法变成能跑的系统",
    "AI产品": "我能用AI工具快速搭出产品原型验证想法，从需求分析到落地交付都有经验",
    "AI运营": "我用AI工具做过内容分发和自动化运营的尝试，对如何用AI提升运营效率有实操经验",
    "测试": "我有实车测试和台架测试经验，熟悉CAN/LIN通信和诊断协议，Python自动化测试脚本也写过",
    "车联网": "我做过车联网相关的测试工作，对OTA、V2X、车载以太网都有了解，也会用Python写自动化验证脚本",
    "座舱": "我对智能座舱的语音助手、大模型集成很感兴趣，测试经验能快速上手座舱的功能验证",
    "Python": "Python是我主力语言，写过爬虫、自动化脚本、数据处理全链路，能独立交付完整项目",
    "知识库": "我搭过RAG知识库系统，知道怎么切分文档、选embedding模型、调检索策略",
    "默认": "我擅长用AI工具解决实际业务问题，独立交付过完整的自动化项目，能快速上手干活",
}

# 招呼语模板（JD角色 → 开场白）
GREETING_TEMPLATES = {
    "AI应用": "看到贵司的{title}岗位，{bg}。想了解一下这个岗位主要负责哪个业务方向的产品或场景？",
    "Agent": "看到贵司在招{title}，{bg}。好奇咱们团队主要用哪些Agent框架和工具链？",
    "RPA": "看到贵司的{title}，{bg}。想了解这个岗位主要做哪类流程自动化，电商还是内部系统？",
    "自动化": "看到贵司的{title}，{bg}。这个岗位偏向业务侧的流程自动化还是偏底层的系统开发？",
    "低代码": "看到贵司的{title}，{bg}。咱们主要用哪些低代码平台？Dify/Coze还是影刀？",
    "AI产品": "看到贵司招{title}，{bg}。好奇这个岗位是偏向AI能力的产品化，还是用AI提升现有产品体验？",
    "AI运营": "看到贵司的{title}，{bg}。想了解咱们运营团队目前用了哪些AI工具提效？",
    "测试": "看到贵司的{title}，{bg}。想了解这个岗位的测试对象和主要用到的工具链？",
    "车联网": "看到贵司在招{title}，{bg}。咱们主要做T-BOX还是整车OTA方向的测试？",
    "座舱": "看到贵司的{title}，{bg}。想了解一下这个岗位主要负责座舱的哪些功能模块？",
    "Python": "看到贵司的{title}，{bg}。想了解这个岗位的技术栈和主要业务场景？",
    "知识库": "看到贵司的{title}，{bg}。咱们的知识库主要服务内部还是对外产品？",
    "默认": "看到贵司的{title}，{bg}。期待进一步了解这个岗位的具体方向和团队情况！",
}


def generate_greeting(title: str, desc: str, company: str = "") -> str:
    """根据JD内容智能生成个性化招呼语。

    匹配顺序: JD关键词 → 默认
    返回: 50-100字的自然招呼语
    """
    combined = ((title or "") + " " + (desc or "")).lower()

    # 按优先级匹配角色类型
    ROLE_PRIORITY = [
        "Agent", "AI应用", "AI产品", "AI运营", "RPA",
        "低代码", "自动化", "车联网", "座舱", "测试",
        "知识库", "Python",
    ]

    matched_role = "默认"
    for role in ROLE_PRIORITY:
        role_lower = role.lower()
        # 模糊匹配
        if role_lower in combined or any(kw in combined for kw in role_lower.split()):
            matched_role = role
            break

    bg = USER_BG.get(matched_role, USER_BG["默认"])
    template = GREETING_TEMPLATES.get(matched_role, GREETING_TEMPLATES["默认"])

    # 限制招呼语总长度（Boss有字数限制）
    greeting = template.format(title=title[:20], bg=bg)
    if len(greeting) > 120:
        greeting = greeting[:117] + "..."

    return greeting


# 常用城市代码
CITY_CODES = {
    "全国": "100010000",
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "武汉": "101200100",
    "南京": "101190100",
    "重庆": "101040100",
    "苏州": "101190400",
    "合肥": "101220100",
    "西安": "101110100",
    "长沙": "101250100",
    "东莞": "101281600",
    "天津": "101030100",
    "厦门": "101230200",
    "佛山": "101280500",
    "无锡": "101190200",
    "珠海": "101280700",
    "宁波": "101210400",
}


def parse_args(cfg: dict):
    p = argparse.ArgumentParser(description="Boss直聘自动投递")
    p.add_argument("--job", default=None, help="单个搜索岗位名")
    p.add_argument(
        "--jobs",
        default=None,
        help="多个搜索词，逗号分隔（如：智驾测试,ADAS测试）",
    )
    p.add_argument("--city", default=None, help="单个城市")
    p.add_argument(
        "--cities",
        default=None,
        help="多个城市，逗号分隔（如：深圳,上海,广州）",
    )
    p.add_argument("--count", type=int, default=None, help="每个城市+关键词的投递上限")
    p.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="最低评分（0=全投不过滤）",
    )
    p.add_argument(
        "--daily",
        action="store_true",
        help="日常模式：使用 config 中的 target_cities + search_keywords",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="清除暂停锁并继续运行",
    )
    return p.parse_args()


def run_single_cycle(page, search_tab, city: str, keyword: str, count: int, min_score: int, cfg: dict):
    """在单个城市搜索一个关键词，完成投递循环。返回 (applied, skipped, failed) 计数。"""
    skill_dir = Path(__file__).parent
    city_code = CITY_CODES.get(city, "100010000")
    log_file = skill_dir / f"boss-{city}-log.json"

    log = load_log(log_file)
    seen_titles = set()
    for e in log.get("applied", []) + log.get("skipped", []):
        t = e.get("job", "")
        if t:
            seen_titles.add(t)

    applied_count = 0
    skipped_count = 0
    failed_count = 0

    search_url = (
        f"https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}"
        f"&degree=203,202&experience=101,108,102,103"
    )
    print(f"\n{'='*60}")
    print(f"📍 {city} | 🔍 {keyword} | 🎯 上限 {count} 份")
    print(f"{'='*60}")

    try:
        search_tab.get(search_url)
    except Exception:
        search_tab = page.new_tab(search_url)
    time.sleep(4 + random.uniform(0, 3))

    # 检查登录
    if "login" in search_tab.url or "user/?ka" in search_tab.url:
        if record_login_fail():
            print("  💤 已进入睡眠模式，停止所有投递")
            SKILL_DIR_APPLY = Path(__file__).parent
            return 0, 0, 0
        user_input = _safe_input_or_skip("⚠️  未登录，请在浏览器中登录后按 Enter（非交互模式自动跳过）...")
        if user_input is None:
            print("  跳过当前城市+关键词")
            return 0, 0, 0
        try:
            search_tab.get(search_url)
        except Exception:
            search_tab = page.new_tab(search_url)
        time.sleep(4)
    else:
        record_login_ok()

    # 检查 Boss 风控/验证/封号页面
    BLOCK_SIGNALS = ["verify", "captcha", "abnormal", "block", "forbidden",
                     "安全验证", "账号异常", "行为异常", "ip限制"]
    current_url = search_tab.url.lower()
    page_text = ""
    try:
        page_text = (search_tab.ele("body") or search_tab).text[:500].lower() if hasattr(search_tab, "ele") else ""
    except Exception:
        pass
    for sig in BLOCK_SIGNALS:
        if sig in current_url or (page_text and sig in page_text):
            print(f"🚫 Boss 风控触发 ({sig})！停止投递，等待几小时后再试")
            return 0, 0, 0

    # 滚动加载更多，直到投满或没有新卡片
    page_num = 1
    consecutive_no_new = 0
    consecutive_zero_score = 0
    max_pages = 6  # 每关键词最多翻6页，防止无限滚动

    while applied_count < count and consecutive_no_new < 3 and page_num <= max_pages and consecutive_zero_score < 3:
        # ── 终端/Chrome存活检查 ──
        if check_should_stop(page):
            print(f"  ⏸️  {STOP_REASON}")
            return applied_count, skipped_count, failed_count

        time.sleep(2 + random.uniform(0, 2))

        cards = search_tab.eles(".job-card-wrap")
        if not cards:
            print("  未找到岗位卡片，停止")
            break

        # 收集当前页未处理的标题
        pending = []
        for c in cards:
            t = c.ele(".job-name")
            title = t.text.strip() if t and t.text else ""
            if title and title not in seen_titles:
                pending.append(title)

        if not pending:
            # 滚到底加载更多
            prev_count = len(cards)
            search_tab.run_js("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2 + random.uniform(0, 2))
            new_cards = search_tab.eles(".job-card-wrap")
            new_count = len(new_cards)
            if new_count <= prev_count:
                consecutive_no_new += 1
                print(f"  第 {page_num} 页无新岗位 (连续 {consecutive_no_new}/3)")
            else:
                consecutive_no_new = 0
                print(f"  加载更多：{prev_count} → {new_count} 个")
            page_num += 1
            continue

        print(f"  待处理 {len(pending)} 个（第 {page_num} 页）")
        consecutive_no_new = 0

        page_all_zero = True
        for title in pending:
            if applied_count >= count:
                break
            # ── 每个岗位处理前检查一次 ──
            if check_should_stop(page):
                print(f"  ⏸️  {STOP_REASON}")
                return applied_count, skipped_count, failed_count
            seen_titles.add(title)

            # 获取公司名和薪资
            company = (
                search_tab.run_js(f"""
                var cards = document.querySelectorAll(".job-card-wrap");
                for (var c of cards) {{
                    var n = c.querySelector(".job-name");
                    if (n && n.textContent.trim() === {json.dumps(title)}) {{
                        var co = c.querySelector(".boss-name");
                        return co ? co.textContent.trim() : "";
                    }}
                }}
                return "";
            """)
                or ""
            )
            salary = (
                search_tab.run_js(f"""
                var cards = document.querySelectorAll(".job-card-wrap");
                for (var c of cards) {{
                    var n = c.querySelector(".job-name");
                    if (n && n.textContent.trim() === {json.dumps(title)}) {{
                        var s = c.querySelector(".job-salary");
                        return s ? s.textContent.trim() : "";
                    }}
                }}
                return "";
            """)
                or ""
            )

            try:
                # 点击卡片加载详情
                search_tab.run_js(f"""
                    var cards = document.querySelectorAll(".job-card-wrap");
                    for (var c of cards) {{
                        var n = c.querySelector(".job-name");
                        if (n && n.textContent.trim() === {json.dumps(title)}) {{
                            c.click(); break;
                        }}
                    }}
                """)
                time.sleep(2 + random.uniform(0, 2))

                desc_el = search_tab.ele(".job-detail-body") or search_tab.ele(".job-sec-text")
                desc = desc_el.text if desc_el else ""

                score, reason = score_jd(title, desc, cfg)

                # 智能过滤：公司规模/性质/薪资/技术含量
                smart_score, smart_reason = smart_filter(company, title, desc, salary, score, cfg, city=city)
                if smart_score != score:
                    if smart_score == 0:
                        print(f"  [🔴过滤] {company[:15]} | {title[:25]} | {salary} → {smart_reason}")
                        skipped_count += 1
                        log["skipped"].append({
                            "company": company, "job": title, "salary": salary,
                            "score": score, "reason": smart_reason,
                            "city": city, "keyword": keyword,
                            "time": datetime.now().isoformat(),
                        })
                        save_log(log, log_file)
                        continue
                    else:
                        print(f"  [🟡调整] {score}→{smart_score}分 {company[:15]} | {title[:25]} | {salary} → {smart_reason}")
                        score = smart_score
                        reason = reason + "、" + smart_reason

                # ── 深度筛选 v2 (2026-08-07)：标题党检测 + 实习薪资陷阱（本地，零成本）──
                deep_score, deep_reason = deep_filter(company, title, desc, salary, score)
                if deep_score == 0:
                    print(f"  [🔴深度过滤] {company[:15]} | {title[:25]} | {salary} → {deep_reason}")
                    skipped_count += 1
                    log["skipped"].append({
                        "company": company, "job": title, "salary": salary,
                        "score": score, "reason": deep_reason,
                        "city": city, "keyword": keyword,
                        "time": datetime.now().isoformat(),
                    })
                    save_log(log, log_file)
                    continue

                # ── 公司背调 v2 (2026-08-07)：只对即将投递的做，带缓存，失败降级 ──
                try:
                    def _company_eval(comp, cty):
                        q = comp[:8]
                        return search_tab.run_js(f"""
                            (async () => {{
                              try {{
                                const r = await fetch('/wapi/zpgeek/search/joblist.json?scene=1&query={q}&city={city}&page=1&pageSize=15', {{
                                  headers: {{'accept': 'application/json'}}
                                }});
                                const d = await r.json();
                                const list = (d.zpData && d.zpData.jobList) || [];
                                return JSON.stringify(list.map(j => ({{name: j.jobName, brand: j.brandName}})));
                              }} catch(e) {{ return 'ERR:' + e.message; }}
                            }})()
                        """, timeout=20)
                    profile = run_company_background_check(company, city, _company_eval)
                    prof_score, prof_reason = deep_filter(company, title, desc, salary, score, profile=profile)
                    if prof_score == 0:
                        print(f"  [🔴公司背调] {company[:15]} | {title[:25]} → {prof_reason}")
                        skipped_count += 1
                        log["skipped"].append({
                            "company": company, "job": title, "salary": salary,
                            "score": score, "reason": prof_reason,
                            "city": city, "keyword": keyword,
                            "time": datetime.now().isoformat(),
                        })
                        save_log(log, log_file)
                        continue
                except Exception as e:
                    # 背调失败降级：不误杀，正常继续
                    pass

                print(f"  [{score:3d}分] {company[:15]} | {title[:25]} | {salary} → {reason}")

                if score < min_score:
                    skipped_count += 1
                    log["skipped"].append({
                        "company": company,
                        "job": title,
                        "salary": salary,
                        "score": score,
                        "reason": reason,
                        "city": city,
                        "keyword": keyword,
                        "time": datetime.now().isoformat(),
                    })
                    save_log(log, log_file)
                    continue

                page_all_zero = False

                # 检查是否已达沟通上限
                btn_disabled = search_tab.run_js(
                    'var b=document.querySelector(".op-btn-chat"); return b ? b.classList.contains("is-disabled") : false;'
                )
                if btn_disabled:
                    skipped_count += 1
                    log["skipped"].append({
                        "company": company,
                        "job": title,
                        "score": score,
                        "reason": "已沟通过",
                        "city": city,
                        "keyword": keyword,
                        "time": datetime.now().isoformat(),
                    })
                    save_log(log, log_file)
                    print(f"    ⏭️  已沟通过，跳过")
                    continue

                # 生成智能招呼语
                greeting = generate_greeting(title, desc, company)
                print(f"    💬 招呼语: {greeting[:50]}...")

                # 点击"立即沟通"
                search_tab.run_js(
                    'var b=document.querySelector(".op-btn-chat"); if(b) b.click();'
                )
                time.sleep(2 + random.uniform(1, 2))

                # ── 自动点掉 Boss 弹窗（如"今日沟通已达上限"等提示）──
                # 通用逻辑：找可见弹窗，点掉"我知道了/确定/知道了/好的/关闭"类按钮
                search_tab.run_js("""
                    (function() {
                        // 常见弹窗容器
                        var modals = document.querySelectorAll(
                            '.modal, .dialog, .boss-modal, [class*="modal"], [class*="dialog"], ' +
                            '[class*="popup"], [class*="toast"], [class*="notice"]'
                        );
                        for (var m of modals) {
                            if (!m.offsetParent) continue;  // 不可见跳过
                            var btns = m.querySelectorAll('button, a, span[role="button"], div[class*="btn"]');
                            for (var b of btns) {
                                var t = (b.textContent || '').trim();
                                if (t && /知道了|我知道了|确定|好的|确认|继续|关闭|取消|×|✕/.test(t) &&
                                    b.offsetParent !== null && !b.disabled) {
                                    b.click();
                                    return 'clicked';
                                }
                            }
                        }
                        return 'no_modal';
                    })();
                """)
                time.sleep(1)

                # 填入自定义招呼语并发送
                try:
                    search_tab.run_js(f"""
                        (function() {{
                            // 找聊天输入框
                            var input = document.querySelector('[contenteditable="true"]:not([style*="display: none"])');
                            if (!input) {{
                                var textareas = document.querySelectorAll('textarea');
                                for (var t of textareas) {{
                                    if (t.offsetParent !== null) {{ input = t; break; }}
                                }}
                            }}
                            if (!input) return;
                            // 填入招呼语
                            var text = {json.dumps(greeting)};
                            if (input.tagName === 'TEXTAREA') {{
                                input.value = text;
                            }} else {{
                                input.textContent = text;
                            }}
                            input.dispatchEvent(new Event('input', {{bubbles: true}}));
                            input.dispatchEvent(new Event('change', {{bubbles: true}}));
                            // 延迟找发送按钮并点击
                            setTimeout(function() {{
                                var btns = document.querySelectorAll('button, a, span[role="button"]');
                                for (var b of btns) {{
                                    var t = (b.textContent || '').trim();
                                    var cls = (b.className || '') + ' ' + (b.getAttribute('class') || '');
                                    if ((t === '发送' || t.indexOf('发送') > -1 || cls.indexOf('send') > -1)
                                        && b.offsetParent !== null && !b.disabled) {{
                                        b.click(); return;
                                    }}
                                }}
                            }}, 400);
                        }})();
                    """)
                    time.sleep(2 + random.uniform(0, 1))
                except Exception:
                    pass  # 填充失败不影响主流程，已经点了沟通

                # 导回搜索页
                try:
                    search_tab.get(search_url)
                except Exception:
                    search_tab = page.new_tab(search_url)
                time.sleep(3 + random.uniform(0, 2))

                applied_count += 1
                log["applied"].append({
                    "company": company,
                    "job": title,
                    "salary": salary,
                    "score": score,
                    "city": city,
                    "keyword": keyword,
                    "time": datetime.now().isoformat(),
                })
                save_log(log, log_file)
                print(f"    ✅ 已投递 ({applied_count + skipped_count}/{count + skipped_count})")

                # 投递间延迟
                time.sleep(1 + random.uniform(0, 2))

            except Exception as e:
                failed_count += 1
                err = str(e)[:120]
                print(f"    ❌ 失败: {err}")
                log["failed"].append({
                    "job": title,
                    "error": err,
                    "city": city,
                    "keyword": keyword,
                    "time": datetime.now().isoformat(),
                })
                save_log(log, log_file)
                time.sleep(1)

        page_num += 1
        if page_all_zero:
            consecutive_zero_score += 1
            print(f"  本页全不匹配 (连续 {consecutive_zero_score}/3 页无匹配)")
        else:
            consecutive_zero_score = 0

    return applied_count, skipped_count, failed_count


def main():
    cfg = load_config()
    args = parse_args(cfg)

    # ── --resume: 清除暂停锁后退出 ──
    if args.resume:
        resume()
        return

    # ── 启动自检：如果已暂停，直接退出不触发任何操作 ──
    if is_paused():
        reason = "未知"
        try:
            reason = json.loads(PAUSE_FILE.read_text()).get("reason", "未知")
        except Exception:
            pass
        print(f"⏸️  Job Hunter 已暂停")
        print(f"   原因: {reason}")
        print(f"   恢复: python3 boss_apply.py --resume")
        return

    sleep_status = get_sleep_status()
    if sleep_status:
        print(f"💤 睡眠模式: {sleep_status}")
        print(f"   恢复: python3 boss_apply.py --resume")

    # 确定城市列表
    if args.daily or (not args.city and not args.cities):
        cities = args.cities.split(",") if args.cities else cfg.get("target_cities", ["深圳"])
    elif args.cities:
        cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    elif args.city:
        cities = [args.city]
    else:
        cities = cfg.get("target_cities", ["深圳"])

    # 确定搜索词列表
    if args.daily or (not args.job and not args.jobs):
        keywords = args.jobs.split(",") if args.jobs else cfg.get("search_keywords", ["智驾测试"])
    elif args.jobs:
        keywords = [k.strip() for k in args.jobs.split(",") if k.strip()]
    elif args.job:
        keywords = [args.job]
    else:
        keywords = cfg.get("search_keywords", ["智驾测试"])

    count = args.count or cfg.get("default_count", 15)
    min_score = args.min_score if args.min_score is not None else cfg.get("min_score", 30)

    print(f"""
╔══════════════════════════════════════╗
║  🤖 Job Hunter v2 — Boss直聘        ║
╠══════════════════════════════════════╣
║  城市: {', '.join(cities)}         ║
║  搜索: {', '.join(keywords)}        ║
║  每任务上限: {count} 份              ║
║  最低评分: {min_score}               ║
║  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}           ║
╚══════════════════════════════════════╝
""")

    # 连接投递专用 Chrome 2 — 复用已有窗口，不新建
    print("🔗 连接投递专用 Chrome...")
    from DrissionPage import ChromiumOptions
    from DrissionPage.errors import BrowserConnectError
    opts = ChromiumOptions(read_file=False)
    opts.set_user_data_path(str(Path("~/job-hunter-chrome").expanduser()))
    opts.set_local_port(9222)
    try:
        page = ChromiumPage(addr_or_opts=opts)
    except BrowserConnectError:
        print("❌ Chrome 浏览器未启动或调试端口 (9222) 不可用")
        if not INTERACTIVE:
            pause("Chrome未启动(launchd定时触发)")
        # 记录一次登录失败（Chrome不在=无法登录）
        # 不计入睡眠计数——Chrome不在不等同于登录过期
        return
    # 用现有tab避免Boss掉登录(不新建tab,boss用了tab级session隔离)
    search_tab = page.get_tab(page.tab_ids[0])

    total_applied = 0
    total_skipped = 0
    total_failed = 0
    DAILY_LIMIT = 150  # 每天 150 份封顶（Boss 上限），弹窗已自动点掉
    MAX_RETRIES = 2     # 断连最多重试 2 次（之前 10 次导致封号）

    for city in cities:
        if not city.strip():
            continue
        # ── 终端/Chrome存活检查 ──
        if check_should_stop(page):
            break

        for keyword in keywords:
            if not keyword.strip():
                continue
            # ── 终端/Chrome存活检查 ──
            if check_should_stop(page):
                break
            try:
                a, s, f = run_single_cycle(
                    page, search_tab, city.strip(), keyword.strip(),
                    count, min_score, cfg
                )
                total_applied += a
                total_skipped += s
                total_failed += f
            except Exception as e:
                total_failed += 1
                print(f"  ❌ 错误: {str(e)[:80]}")

            # ── 每日限额检查（每个关键词后都查，不藏在休息块里）──
            if total_applied >= DAILY_LIMIT:
                print(f"\n  🛑 已达每日安全上限 {DAILY_LIMIT} 份，停止")
                break

            # 关键词间休息——模拟人类浏览节奏（15-25 秒防封）
            if city != cities[-1] or keyword != keywords[-1]:
                rest = 15 + random.uniform(0, 10)
                print(f"\n  ☕ 休息 {rest:.0f}s ... (今日已投 {total_applied}/{DAILY_LIMIT})\n")
                time.sleep(rest)

        if total_applied >= DAILY_LIMIT:
            break

    print(f"""
╔══════════════════════════════════════╗
║  {"⏸️  已暂停" if SHOULD_STOP else "全部完成":34s}║
║  ✅ 投递: {total_applied}  ⏭️ 跳过: {total_skipped}  ❌ 失败: {total_failed}  ║""")
    if SHOULD_STOP:
        print(f"║  原因: {STOP_REASON[:32]:32s}║")
    print("╚══════════════════════════════════════╝")

    # 生成报告
    print_terminal_summary()
    report_path = generate_html()
    print(f"\n📄 报告已生成: {report_path}")


if __name__ == "__main__":
    main()

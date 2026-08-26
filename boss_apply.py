#!/usr/bin/env python3
"""Boss直聘自动投递脚本 v2 — 多城市 + 多关键词 + 自动报告"""

import argparse
import json
import os
import random
import signal
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from DrissionPage import ChromiumPage, ChromiumOptions
from typing import Optional

from shared import load_config, score_jd, smart_filter, get_chrome_opts, kill_switch_check, kill_switch_off, kill_switch_on, kill_switch_status, CITY_CODES
from store import (
    ensure_migrated, migrate_legacy_logs, list_city_titles, company_applied_recently,
    record_application, count_applied_since,
)
from deep_filter import deep_filter, run_company_background_check
from match_engine import explain_match
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
RECOVERY_FILE = SKILL_DIR / ".recovery_until"  # 解封恢复期截止时间


# ── 安全护栏（2026-08-16 新增：封号复盘后落地）──
# 根因：8/11 单日191份+单时47+夜间+重复投同公司；8/15 解封当天5小时86份。
# 全部速率护栏在两次封号时都不存在，本段把这些约束变成代码强制。

def get_safety(cfg: dict) -> dict:
    """读取安全护栏配置，缺省字段用保守默认值补齐。"""
    defaults = {
        "recovery_days": 3,          # 解封后恢复期天数
        "recovery_daily_cap": 25,    # 恢复期每日上限（跨进程）
        "normal_daily_cap": 50,      # 正常期每日上限（跨进程）
        "hourly_cap": 8,             # 单小时上限 → 休息30分钟
        "night_ban_start": 22,       # 夜间禁投开始
        "night_ban_end": 8,          # 夜间禁投结束（次日）
        "dedup_days": 7,             # 同公司×同城 N 天内不重复投
    }
    defaults.update(cfg.get("safety") or {})
    return defaults


def load_recovery_until() -> Optional[str]:
    if RECOVERY_FILE.exists():
        try:
            return RECOVERY_FILE.read_text().strip()
        except Exception:
            return None
    return None


def is_recovery_active() -> bool:
    """恢复期内 → 用降量上限；过期/未设置 → 正常上限。"""
    until = load_recovery_until()
    if not until:
        return False
    try:
        return datetime.now() < datetime.fromisoformat(until)
    except Exception:
        return False


def in_night_window(s: dict) -> bool:
    """当前是否在夜间禁投时段（支持跨午夜，如 22:00-08:00）。"""
    start, end = s.get("night_ban_start", 22), s.get("night_ban_end", 8)
    hour = datetime.now().hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _signal_handler(signum, frame):
    global SHOULD_STOP, STOP_REASON
    names = {signal.SIGHUP: "终端关闭(SIGHUP)", signal.SIGTERM: "SIGTERM",
             signal.SIGINT: "Ctrl+C(SIGINT)"}
    SHOULD_STOP = True
    STOP_REASON = names.get(signum, f"信号{signum}")
    if signum == signal.SIGINT:
        # A6: Ctrl+C = 用户只想结束本轮，不写暂停锁。
        # 旧行为会把 .paused 写下去，导致 launchd 定时任务停摆到手动 --resume。
        # SIGHUP/SIGTERM（真·终端关闭/被杀）仍走 pause() 写锁防重拉。
        print("\n🛑 收到 Ctrl+C，手动中断，本轮结束（未写暂停锁，定时任务照常）")
        return
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
    "采购": "我独立负责过小型工程项目采购全流程，从需求拆解、供应商寻源、询价比价到合同履约和付款控制都亲手跑通，还用Python搭过3万+条比价数据的自动化台账",
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
    "采购": "看到贵司的{title}岗位，{bg}。想了解这个岗位主要负责哪类物资品类，是IT/办公设备还是工程项目物料？",
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
    # 采购岗专属人设优先（采购JD常含"自动化/测试/Python"等词，防止被AI人设截胡）
    if "采购" in (title or ""):
        matched_role = "采购"

    bg = USER_BG.get(matched_role, USER_BG["默认"])
    template = GREETING_TEMPLATES.get(matched_role, GREETING_TEMPLATES["默认"])

    # 限制招呼语总长度（Boss有字数限制）
    greeting = template.format(title=title[:20], bg=bg)
    if len(greeting) > 120:
        greeting = greeting[:117] + "..."

    return greeting


# CITY_CODES 已统一到 shared.py（P2-T6），本文件从 shared 导入。


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
        "--migrate-logs",
        action="store_true",
        help="把旧 *-log.json 导入 SQLite 单一事实源（幂等，可重复执行）",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="清除暂停锁并继续运行",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：只搜索/评分/过滤，绝不投递，输出待投递计划",
    )
    p.add_argument(
        "--report",
        action="store_true",
        help="只读模式：扫描今日日志并输出统计报告，不打开浏览器",
    )
    p.add_argument("--kill-on", action="store_true", help="恢复 kill switch（允许投递）")
    p.add_argument("--kill-off", type=str, metavar="原因", help="关闭 kill switch（禁止投递）")
    p.add_argument("--kill-status", action="store_true", help="查看 kill switch 状态")
    p.add_argument(
        "--recovery",
        nargs="?",
        const="auto",
        default=None,
        metavar="天数",
        help="设置解封恢复期（默认取 config safety.recovery_days），期间使用降量上限",
    )
    p.add_argument("--safety-status", action="store_true", help="查看安全护栏状态与当前生效上限")
    return p.parse_args()


def resume_version_for(title: str) -> str:
    """按岗位标题判断应使用的简历版本（A/B/C/D）"""
    t = (title or "").lower()
    if any(k in t for k in ["采购", "寻源", "招采", "sourcing", "buyer", "供应商管理"]):
        return "D-采购"
    if any(k in t for k in ["车联网", "车载", "智能座舱", "ota", "adas", "t-box", "v2x", "整车", "台架", "hil", "can", "三电", "电池", "bms", "车机", "导航测试", "汽车电子", "自动驾驶", "智能驾驶"]):
        return "C-车联网"
    if any(k in t for k in ["实施", "解决方案", "技术支持", "数字化", "顾问", "运营"]):
        return "B-解决方案"
    if any(k in t for k in ["ai", "llm", "agent", "rag", "dify", "coze", "大模型", "智能体", "知识库", "工作流", "prompt"]):
        return "A-AI应用"
    return "其他"


def _looks_disconnected(e) -> bool:
    """判断异常是否为 tab↔页面 websocket 断连（Boss 页重载 / session 掉线导致引用失效）。"""
    s = str(e).lower()
    return any(k in s for k in ("连接", "断开", "disconnect", "websocket", "connection"))


def _recover_search_tab(page, search_tab, url):
    """返回一个已导航到 url、可正常通信的 Boss 搜索页 tab。

    现有引用还活就直接复用；断了则从 page 里找现存的 zhipin tab（保登录态）；
    实在没有才新建。Boss 用 tab 级 session 隔离，故优先复用、尽量不 new_tab，
    避免新 tab 掉登录。救不活则向上抛（交外层熔断）。"""
    # 1) 现有引用仍连通 → 直接导航复用（最快路径）
    try:
        search_tab.get(url)
        time.sleep(4 + random.uniform(0, 3))
        return search_tab
    except Exception:
        pass
    # 2) page 里找现存的 zhipin tab（不新建，保登录态）
    for tid in list(page.tab_ids):
        try:
            t = page.get_tab(tid)
            if "zhipin.com" in (t.url or ""):
                t.get(url)
                time.sleep(4 + random.uniform(0, 3))
                return t
        except Exception:
            continue
    # 3) 兜底：新建（可能丢登录态，但优于一直用死引用）
    new_tab = page.new_tab(url)
    time.sleep(4 + random.uniform(0, 3))
    return new_tab


def _reset_after_apply(page, search_tab, search_url):
    """R2 同页续投：清掉上一份投递在页面上的残留状态，替代整页 reload。

    整页刷新原来保证的"干净初始态"，用最小 DOM 操作等价复现（只删节点，不点任何按钮）：
      1. 残留聊天输入框/会话 → 移除节点（防下一轮 _chat_signal 误判、招呼语误发进上一会话）
      2. 残留职位详情文本   → 清空（防 score_jd 读到上一岗位 JD）
      3. 残留"立即沟通"按钮 → 移除（防误点到上一岗位的沟通入口）
    卡片列表本身不动：外层循环靠 seen_titles 游标跳过已处理卡片，滚动位置/懒加载全保留。
    仅当 tab 已被导航离开搜索页时才回退为整页加载（等价旧自愈路径，属罕见分支）。
    返回可继续使用的 search_tab。
    """
    try:
        url = search_tab.url or ""
    except Exception:
        url = ""
    if "zhipin.com" not in url or "/web/geek/job" not in url:
        # tab 被导航走（如整页跳到聊天页）→ 沿用旧的整页加载自愈
        try:
            search_tab.get(search_url)
        except Exception:
            search_tab = page.new_tab(search_url)
        time.sleep(3 + random.uniform(0, 2))
        return search_tab
    try:
        search_tab.run_js("""
            var eds = document.querySelectorAll('[contenteditable="true"]');
            for (var i = 0; i < eds.length; i++) {
                if (eds[i].offsetParent !== null) eds[i].remove();
            }
            var tas = document.querySelectorAll('textarea');
            for (var j = 0; j < tas.length; j++) {
                if (tas[j].offsetParent !== null) tas[j].remove();
            }
            var b = document.querySelector('.op-btn-chat');
            if (b) b.remove();
            var d1 = document.querySelector('.job-detail-body');
            if (d1) d1.textContent = '';
            var d2 = document.querySelector('.job-sec-text');
            if (d2) d2.textContent = '';
        """)
    except Exception:
        pass
    time.sleep(0.5 + random.uniform(0, 0.8))
    return search_tab


def _record_outcome(city, company, title, salary, keyword, score, reason, *,
                    decision="skipped", status="SKIPPED", resume_version="",
                    event=None, event_error=None, traceback=None):
    """把一次投递结果写入 SQLite 单一事实源（store.py）。

    A8：traceback 为可选增强字段——异常失败时随事件 payload 落库完整堆栈，
    原有 reason(err) 字段格式不变。
    """
    extra_payload = {"traceback": traceback} if traceback else None
    record_application(
        platform="boss", city=city, company=company, title=title, salary=salary,
        keyword=keyword, score=score, resume_version=resume_version,
        decision=decision, status=status, reason=reason, verified=0,
        event_type=event or decision, event_error=event_error,
        extra_payload=extra_payload,
    )


def _dismiss_modals(tab) -> str:
    """点掉常见弹窗，返回首个可见弹窗文案（用于识别"沟通上限"等拦截提示）。"""
    try:
        r = tab.run_js("""
            (function() {
                var modals = document.querySelectorAll(
                    '.modal, .dialog, .boss-modal, [class*="modal"], [class*="dialog"], ' +
                    '[class*="popup"], [class*="toast"], [class*="notice"]'
                );
                var firstText = '';
                for (var m of modals) {
                    if (!m.offsetParent) continue;
                    var t = (m.textContent || '').trim();
                    if (!firstText && t) firstText = t.slice(0, 120);
                    var btns = m.querySelectorAll('button, a, span[role="button"], div[class*="btn"]');
                    for (var b of btns) {
                        var bt = (b.textContent || '').trim();
                        if (bt && /知道了|我知道了|确定|好的|确认|继续|关闭|取消|×|✕/.test(bt) &&
                            b.offsetParent !== null && !b.disabled) {
                            b.click();
                            return firstText || 'clicked';
                        }
                    }
                }
                return firstText || 'no_modal';
            })();
        """, as_expr=True)
        return str(r or "no_modal")
    except Exception:
        return "no_modal"


def _chat_signal(tab) -> str:
    """验证点击"立即沟通"后的会话状态信号。

    返回：
      'input'  同页出现聊天输入框（可原地填发）
      'panel'  同页出现聊天面板
      'already' 按钮已变"已沟通/disabled"（Boss 已确认沟通，聊天在独立聊天页）
      ''       以上都没有（视为未打开）
    """
    try:
        r = tab.run_js("""
            (function() {
                var ed = document.querySelector('[contenteditable="true"]');
                if (ed && ed.offsetParent !== null) return 'input';
                var tas = document.querySelectorAll('textarea');
                for (var t of tas) { if (t.offsetParent !== null) return 'input'; }
                var b = document.querySelector('.op-btn-chat');
                if (b && (b.classList.contains('is-disabled') || /已沟通/.test(b.textContent || ''))) return 'already';
                var panel = document.querySelector('.chat-panel, .chat-detail, .chat-container, [class*="chat-detail"]');
                if (panel && panel.offsetParent !== null) return 'panel';
                return '';
            })();
        """, as_expr=True)
        return str(r or "")
    except Exception:
        return ""


def _chat_opened(tab) -> bool:
    """旧接口兼容：只要出现任一打开信号就算已打开（A7）。"""
    return _chat_signal(tab) in ("input", "panel", "already")


def _send_greeting_via_chat(page, search_tab, company: str, greeting: str) -> tuple[bool, str]:
    """Boss 新版"立即沟通"不弹输入框：去聊天页找目标会话补发招呼语。

    返回 (是否已验证发出, 说明)。找不到会话/发送未验证 → False（保守判 UNCERTAIN）。
    """
    chat_tab = None
    created = False
    try:
        for tid in list(page.tab_ids):
            t = page.get_tab(tid)
            if "web/geek/chat" in (t.url or ""):
                chat_tab = t
                break
        created = chat_tab is None
        if created:
            chat_tab = page.new_tab("https://www.zhipin.com/web/geek/chat")
        else:
            chat_tab.get("https://www.zhipin.com/web/geek/chat")
        time.sleep(4 + random.uniform(0, 2))
        # 滚动让会话列表加载完
        for _ in range(3):
            try:
                chat_tab.run_js("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            time.sleep(0.6)

        search = (company or "")[:8]
        r = "not_found"
        # 会话可能延迟出现：最多重试 3 次，每次多滚一点
        for attempt in range(3):
            if attempt:
                time.sleep(3)
                try:
                    chat_tab.run_js("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                time.sleep(1)
            r = chat_tab.run_js(f"""
                var lis = document.querySelectorAll('li');
                for (var i=0; i<lis.length; i++) {{
                    var nb = lis[i].querySelector('.name-box');
                    if (nb && (nb.textContent || '').indexOf({json.dumps(search)}) > -1) {{
                        nb.click();
                        return 'clicked';
                    }}
                }}
                return 'not_found';
            """)
            if r == "clicked":
                break
        time.sleep(2 + random.uniform(0, 1))
        ok = _fill_and_send(chat_tab, greeting)
        if created:
            try:
                chat_tab.close()
            except Exception:
                pass
        if r == "clicked" and ok:
            return True, "聊天页补发成功"
        if r == "clicked":
            return False, "已找到会话但发送未验证"
        return False, "聊天页未找到会话(可能已用默认招呼语)"
    except Exception as e:
        return False, f"聊天页补发异常:{str(e)[:60]}"


def _fill_and_send(tab, greeting: str) -> bool:
    """填充招呼语并发送，验证输入框清空/会话关闭。返回 True=已验证发出。"""
    try:
        r = tab.run_js(f"""
            (function() {{
                var ed = document.querySelector('[contenteditable="true"]:not([style*="display: none"])');
                if (!ed || ed.offsetParent === null) {{
                    var tas = document.querySelectorAll('textarea');
                    for (var t of tas) {{ if (t.offsetParent !== null) {{ ed = t; break; }} }}
                }}
                if (!ed) return 'NO_INPUT';
                ed.focus();
                if (ed.tagName === 'TEXTAREA') {{
                    ed.value = {json.dumps(greeting)};
                    ed.dispatchEvent(new Event('input', {{bubbles: true}}));
                    ed.dispatchEvent(new Event('change', {{bubbles: true}}));
                }} else {{
                    document.execCommand('selectAll', false, null);
                    document.execCommand('insertText', false, {json.dumps(greeting)});
                }}
                var cur = ed.tagName === 'TEXTAREA' ? ed.value : ed.textContent;
                if (!cur || !cur.trim()) return 'EMPTY_AFTER_FILL';
                var btns = document.querySelectorAll('button, a, span[role="button"]');
                for (var b of btns) {{
                    var t = (b.textContent || '').trim();
                    var cls = (b.className || '') + ' ' + (b.getAttribute('class') || '');
                    if ((t === '发送' || t.indexOf('发送') > -1 || cls.indexOf('send') > -1)
                        && b.offsetParent !== null && !b.disabled) {{
                        b.click();
                        return 'SENT_CLICKED';
                    }}
                }}
                ed.dispatchEvent(new KeyboardEvent('keydown', {{
                    key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                    bubbles: true, cancelable: true
                }}));
                return 'ENTER_KEY';
            }})();
        """, as_expr=True)
        time.sleep(2 + random.uniform(0, 1))
    except Exception as e:
        print(f"    ⚠️ 填发招呼语异常: {e}")
        return False

    if r is None:
        return False
    if r in ("NO_INPUT", "EMPTY_AFTER_FILL"):
        return False
    # 验证：输入框已清空 = 发出；输入框已消失 = 会话关闭（同样视为发出）
    try:
        state = tab.run_js("""
            var ed = document.querySelector('[contenteditable="true"]');
            if (ed) return ed.textContent.trim() === '' ? 'cleared' : 'has_text';
            var tas = document.querySelectorAll('textarea');
            for (var t of tas) { if (t.offsetParent !== null) return t.value.trim() === '' ? 'cleared' : 'has_text'; }
            return 'no_input';
        """)
    except Exception:
        state = ""
    if state is None:
        return False
    return state != "has_text"


# ── A8：投递单岗位流程拆分（纯结构性重构，异常语义逐点保持）──
# 原巨型 try 循环体按职责拆为四步，调用方 run_single_cycle 的 try/except 边界、
# 捕获范围、continue/break 与计数逻辑均与拆分前一致：
#   _prepare_job_context   取详情/评分/过滤链（smart/deep/背调/五维）→ "skip" 或 "proceed"
#   _execute_apply         点击沟通→弹窗/信号验证→填发招呼语 → "failed" 或 "sent"
#   _handle_apply_failure  异常分类记录 + 断连自愈 → (search_tab, 是否 break)
#   _cleanup_after_attempt 投后收尾（R2 同页续投重置）
# ctx 为共享可变上下文，承载跨函数的 score/reason/greeting/passed_min_score，
# 使 except 处理器读到的变量状态与拆分前局部变量完全一致。

def _prepare_job_context(search_tab, city, keyword, title, company, salary,
                         cfg, min_score, ctx) -> str:
    """准备阶段：点击卡片加载详情 → JD评分 → 过滤链 → 最低分门槛 → 沟通按钮检查 → 招呼语。

    过滤链任一环命中即打印+落库并返回 "skip"（调用方计 skipped 后 continue）；
    全部通过返回 "proceed"。ctx 实时回写 score/reason；
    passed_min_score 在跨过门槛后置 True（对应原 page_all_zero=False 的时机）。
    """
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
    ctx["score"], ctx["reason"] = score, reason

    # 智能过滤：公司规模/性质/薪资/技术含量
    smart_score, smart_reason = smart_filter(company, title, desc, salary, score, cfg, city=city)
    if smart_score != score:
        if smart_score == 0:
            print(f"  [🔴过滤] {company[:15]} | {title[:25]} | {salary} → {smart_reason}")
            _record_outcome(city, company, title, salary, keyword, score,
                            smart_reason, event="smart_filter")
            return "skip"
        else:
            print(f"  [🟡调整] {score}→{smart_score}分 {company[:15]} | {title[:25]} | {salary} → {smart_reason}")
            score = smart_score
            reason = reason + "、" + smart_reason
            ctx["score"], ctx["reason"] = score, reason

    # ── 深度筛选 v2 (2026-08-07)：标题党检测 + 实习薪资陷阱（本地，零成本）──
    deep_score, deep_reason = deep_filter(company, title, desc, salary, score)
    if deep_score == 0:
        print(f"  [🔴深度过滤] {company[:15]} | {title[:25]} | {salary} → {deep_reason}")
        _record_outcome(city, company, title, salary, keyword, score,
                        deep_reason, event="deep_filter")
        return "skip"

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
            _record_outcome(city, company, title, salary, keyword, score,
                            prof_reason, event="company_profile")
            return "skip"
    except Exception as e:
        # 背调失败降级：不误杀，正常继续
        pass

    # ── 五维评估引擎（2026-08-26）：资格层之上的评估层，只记录不拦截 ──
    # verdict/total 追加进 reason → 随 _record_outcome/record_application 落库
    try:
        match_result = explain_match(title, desc, company=company,
                                     salary=salary, city=city, cfg=cfg)
        md = match_result["dimensions"]
        reason += " |五维{}分:{}{}".format(
            match_result["total"], match_result["verdict"],
            ("；风险:" + "、".join(match_result["risks"][:2])) if match_result["risks"] else "")
        ctx["reason"] = reason
        print(f"  [🎯] {match_result['total']}分 {match_result['verdict']} | "
              f"技术{md['technical']['weighted']:.0f}/30 方向{md['direction']['weighted']:.0f}/30 "
              f"经验{md['experience']['weighted']:.0f}/15 文化{md['culture']['weighted']:.0f}/15 "
              f"地点{md['location']['weighted']:.0f}/10")
    except Exception as me:
        match_result = None
        print(f"  [⚠️五维评估异常(不拦截)] {str(me)[:60]}")

    print(f"  [{score:3d}分] {company[:15]} | {title[:25]} | {salary} → {reason}")

    if score < min_score:
        _record_outcome(city, company, title, salary, keyword, score,
                        reason, event="below_min_score")
        return "skip"

    ctx["passed_min_score"] = True

    # 检查是否已达沟通上限
    btn_disabled = search_tab.run_js(
        'var b=document.querySelector(".op-btn-chat"); return b ? b.classList.contains("is-disabled") : false;'
    )
    if btn_disabled:
        _record_outcome(city, company, title, salary, keyword, score,
                        "已沟通过", event="already_chatted")
        print(f"    ⏭️  已沟通过，跳过")
        return "skip"

    # 生成智能招呼语
    greeting = generate_greeting(title, desc, company)
    ctx["greeting"] = greeting
    print(f"    💬 招呼语: {greeting[:50]}...")
    return "proceed"


def _execute_apply(page, search_tab, city, keyword, title, company, salary, ctx) -> dict:
    """执行阶段：点击「立即沟通」→ 弹窗处理/拦截识别 → 会话信号验证 → 填发招呼语。

    返回 {"action": "failed"}（弹窗拦截/会话未打开，FAILED 落库在本函数内完成，
    调用方计 failed 后 continue）；或
    {"action": "sent", "status", "decision", "verified", "verify_note"}。
    """
    # ── 点击"立即沟通" + 验证（A7：代码没报错 ≠ 业务动作成功）──
    search_tab.run_js(
        'var b=document.querySelector(".op-btn-chat"); if(b) b.click();'
    )
    time.sleep(2 + random.uniform(1, 2))

    # 弹窗处理 + 拦截识别（沟通上限/频繁等 → 记 FAILED，不记 applied）
    modal_text = _dismiss_modals(search_tab)
    if any(k in modal_text for k in ["上限", "频繁", "限制", "无法", "验证", "封禁", "异常"]):
        _record_outcome(city, company, title, salary, keyword, ctx["score"],
                        f"弹窗拦截:{modal_text[:60]}", decision="failed",
                        status="FAILED", event="apply_blocked")
        print(f"    🚫 弹窗拦截: {modal_text[:60]} → FAILED")
        return {"action": "failed"}

    signal = _chat_signal(search_tab)
    if signal == "":
        _record_outcome(city, company, title, salary, keyword, ctx["score"],
                        "点击立即沟通后未检测到会话/已沟通信号", decision="failed",
                        status="FAILED", event="chat_not_opened")
        print(f"    ❌ 会话未打开（未检测到输入框/已沟通信号）→ FAILED")
        return {"action": "failed"}

    greeting = ctx.get("greeting", "")
    if signal in ("input", "panel"):
        greeting_ok = _fill_and_send(search_tab, greeting)
        if greeting_ok:
            app_status, app_decision, verified, verify_note = "APPLIED", "applied", 1, "招呼语已发送并验证"
        else:
            app_status, app_decision, verified, verify_note = "UNCERTAIN", "uncertain", 0, "会话已打开但发送未验证"
    else:
        # 'already'：Boss 已确认沟通（按钮变已沟通），去聊天页补发招呼语
        sent, note = _send_greeting_via_chat(page, search_tab, company, greeting)
        if sent:
            app_status, app_decision, verified, verify_note = "APPLIED", "applied", 1, note
        else:
            app_status, app_decision, verified, verify_note = "UNCERTAIN", "uncertain", 0, note
    print(f"    {'✅' if verified else '⚠️'} {verify_note}")

    return {"action": "sent", "status": app_status, "decision": app_decision,
            "verified": verified, "verify_note": verify_note}


def _handle_apply_failure(page, search_tab, search_url, city, keyword, title,
                          company, salary, ctx, e):
    """失败处理阶段：原巨型 except 的逐语句提取——分类记录 + 断连自愈。

    返回 (search_tab, should_break)：should_break=True 表示断连且重连仍失败，
    调用方须 break 提前结束当前关键词（与拆分前 except 内 break 语义一致）。
    """
    err = str(e)[:120]
    trace = traceback.format_exc()
    print(f"    ❌ 失败: {err}")
    should_break = False
    # ── tab↔页面断连自愈：重绑活 tab，后续卡片不再全废（Boss 页重载/session 掉线）──
    if _looks_disconnected(e):
        try:
            search_tab = _recover_search_tab(page, search_tab, search_url)
            print("    🔌 检测到页面断连，已重新连接搜索页 tab")
        except Exception as re_e:
            print(f"    🔌 重连仍失败（{str(re_e)[:60]}），本关键词提前结束")
            should_break = True
    _record_outcome(city, company, title, salary, keyword, ctx["score"],
                    f"异常:{err}", decision="failed", status="FAILED",
                    event="apply_exception", event_error=trace,
                    traceback=trace)
    time.sleep(1)
    return search_tab, should_break


def _cleanup_after_attempt(page, search_tab, search_url):
    """收尾阶段：R2 同页续投 —— 最小 DOM 重置上一份投递的残留状态，不再整页刷新。

    返回（可能被替换的）search_tab。本步保持在记账（applied_count+=1 / 落库 / 打印）
    之前执行，与拆分前顺序一致：reset 抛异常则该岗位只记 FAILED，不产生 APPLIED 记录。
    （投递间延迟 sleep 留在循环体末尾，保持原始语句顺序。）
    """
    return _reset_after_apply(page, search_tab, search_url)


def run_single_cycle(page, search_tab, city: str, keyword: str, count: int, min_score: int, cfg: dict):
    """在单个城市搜索一个关键词，完成投递循环。返回 (applied, skipped, failed) 计数。"""
    skill_dir = Path(__file__).parent
    city_code = CITY_CODES.get(city, "100010000")

    seen_titles = set(list_city_titles(city))

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

    # 进来先把搜索页 tab 救活（断连时复用现存 zhipin tab，避免一直用死引用）
    search_tab = _recover_search_tab(page, search_tab, search_url)

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
            # A8：原 score/reason 局部变量改为共享上下文（异常处理器按拆分前语义读取最新值）
            ctx = {"score": 0, "reason": ""}

            # 获取公司名和薪资（R1：合并为一次卡片遍历）
            _info = search_tab.run_js(f"""
                var cards = document.querySelectorAll(".job-card-wrap");
                for (var c of cards) {{
                    var n = c.querySelector(".job-name");
                    if (n && n.textContent.trim() === {json.dumps(title)}) {{
                        var co = c.querySelector(".boss-name");
                        var sa = c.querySelector(".job-salary");
                        return {{company: co ? co.textContent.trim() : "",
                                salary: sa ? sa.textContent.trim() : ""}};
                    }}
                }}
                return {{company: "", salary: ""}};
            """) or {}
            if not isinstance(_info, dict):
                _info = {}
            company = _info.get("company") or ""
            salary = _info.get("salary") or ""

            # ── 同公司去重（2026-08-16：防跨关键词重复投同公司触发风控）──
            _ddays = (cfg.get("safety") or {}).get("dedup_days", 7)
            if _ddays > 0 and company_applied_recently(city, company, _ddays):
                print(f"  [🔁去重] {company[:15]} | {title[:25]} — {_ddays}天内已投过该公司，跳过")
                skipped_count += 1
                _record_outcome(city, company, title, salary, keyword, 0,
                                f"同公司{_ddays}天内已投(去重)", event="dedup_skip")
                continue

            try:
                # A8 拆分：准备（详情/评分/过滤链）→ 执行（点击沟通/验证）→ 收尾（R2重置）→ 记账
                action = _prepare_job_context(
                    search_tab, city, keyword, title, company, salary,
                    cfg, min_score, ctx
                )
                if ctx.get("passed_min_score"):
                    page_all_zero = False
                if action == "skip":
                    skipped_count += 1
                    continue

                result = _execute_apply(page, search_tab, city, keyword, title,
                                        company, salary, ctx)
                if result["action"] == "failed":
                    failed_count += 1
                    continue

                # R2：同页续投 —— 在 _cleanup_after_attempt 内做最小 DOM 重置；
                # 保持拆分前顺序：reset 成功后才记账（reset 异常 → 只记 FAILED）
                search_tab = _cleanup_after_attempt(page, search_tab, search_url)

                applied_count += 1
                record_application(
                    platform="boss", city=city, company=company, title=title, salary=salary,
                    keyword=keyword, score=ctx["score"], resume_version=resume_version_for(title),
                    decision=result["decision"], status=result["status"],
                    reason=f"{ctx['reason']}；{result['verify_note']}", verified=result["verified"],
                    event_type=result["status"].lower(),
                )
                print(f"    ✅ 已投递 ({applied_count + skipped_count}/{count + skipped_count})"
                      + (" [UNCERTAIN]" if not result["verified"] else ""))

                # 投递间延迟
                time.sleep(1 + random.uniform(0, 2))

            except Exception as e:
                # A8：与拆分前语义一致——若已跨过最低分门槛（原 page_all_zero=False 已执行），
                # 异常路径同样补齐该状态，再进入统一失败处理
                if ctx.get("passed_min_score"):
                    page_all_zero = False
                failed_count += 1
                search_tab, should_break = _handle_apply_failure(
                    page, search_tab, search_url, city, keyword, title,
                    company, salary, ctx, e
                )
                if should_break:
                    break

        page_num += 1
        if page_all_zero:
            consecutive_zero_score += 1
            print(f"  本页全不匹配 (连续 {consecutive_zero_score}/3 页无匹配)")
        else:
            consecutive_zero_score = 0

    return applied_count, skipped_count, failed_count


def _resolve_cities(cfg):
    """城市列表。优先顶层 target_cities;为空则从 city_pools.city_priority 派生(仅 primary+secondary,按优先级)。"""
    c = cfg.get("target_cities") or []
    if c:
        return [x.strip() for x in c if str(x).strip()]
    prio = (cfg.get("city_pools") or {}).get("city_priority") or {}
    order = {"primary": 0, "secondary": 1, "opportunistic": 2}
    keys = list(prio.keys())
    ranked = sorted(keys, key=lambda k: (order.get(prio[k], 3), keys.index(k)))
    return [k for k in ranked if prio.get(k) in ("primary", "secondary")] or ["深圳"]


def _resolve_keywords(cfg):
    """搜索词。优先顶层 search_keywords;为空则拍平 job_pools.keywords(按原 S/A/B 分级顺序,去重)。"""
    k = cfg.get("search_keywords") or []
    if k:
        return [x.strip() for x in k if str(x).strip()]
    pools = (cfg.get("job_pools") or {}).get("keywords") or {}
    seen, out = set(), []
    for tier_kws in (pools.values() if isinstance(pools, dict) else []):
        for kw in tier_kws:
            kw = str(kw).strip()
            if kw and kw not in seen:
                seen.add(kw)
                out.append(kw)
    return out or ["智驾测试"]


def main():
    cfg = load_config()
    args = parse_args(cfg)

    # ── 旧 JSON → SQLite 迁移（P0：单一事实源）──
    if args.migrate_logs:
        migrate_legacy_logs()
        return
    ensure_migrated(verbose=False)

    # ── --resume: 清除暂停锁后退出 ──
    if args.resume:
        resume()
        return

    # ── kill switch 管理命令 ──
    if args.kill_status:
        print(f"🔌 KILL SWITCH 状态: {kill_switch_status()}")
        return
    if args.kill_off:
        kill_switch_off(args.kill_off)
        return
    if args.kill_on:
        kill_switch_on()
        return

    # ── 安全护栏：查看状态 ──
    if args.safety_status:
        s = get_safety(cfg)
        mode = "恢复期(降量)" if is_recovery_active() else "正常期"
        print("🔒 安全护栏状态")
        print(f"  模式: {mode}  恢复期截止: {load_recovery_until() or '未设置'}")
        print(f"  每日上限: {s['recovery_daily_cap'] if is_recovery_active() else s['normal_daily_cap']}/天")
        print(f"  单小时上限: {s['hourly_cap']}/小时（超过休息30分钟）")
        print(f"  夜间禁投: {s['night_ban_start']}:00 - {s['night_ban_end']}:00")
        print(f"  同公司去重: {s['dedup_days']} 天内不重复投")
        return

    # ── 安全护栏：设置解封恢复期 ──
    if args.recovery is not None:
        s = get_safety(cfg)
        days = s["recovery_days"]
        if args.recovery != "auto":
            try:
                days = int(args.recovery)
            except ValueError:
                pass
        until = datetime.now() + timedelta(days=days)
        RECOVERY_FILE.write_text(until.isoformat())
        print(f"✅ 恢复期已设置: {days} 天 → 至 {until.strftime('%Y-%m-%d')}")
        print(f"   期间投递上限: {s['recovery_daily_cap']}/天, {s['hourly_cap']}/小时")
        print(f"   到期后自动回到正常上限: {s['normal_daily_cap']}/天")
        return

    # ── 启动自检：如果已暂停，直接退出不触发任何操作 ──
    # (dry-run 不投递不碰账号，跳过暂停锁，允许离线验证筛选配置)
    if is_paused() and not args.dry_run:
        reason = "未知"
        try:
            reason = json.loads(PAUSE_FILE.read_text()).get("reason", "未知")
        except Exception:
            pass
        print(f"⏸️  Job Hunter 已暂停")
        print(f"   原因: {reason}")
        print(f"   恢复: python3 boss_apply.py --resume")
        return

    # ── Kill Switch 检查（全局开关，优先于一切写操作）──
    if not args.dry_run:
        allowed, kreason = kill_switch_check()
        if not allowed:
            print(f"🛑  KILL SWITCH 已关闭，投递被禁止")
            print(f"   原因: {kreason}")
            print(f"   恢复: python3 boss_apply.py --kill-on  # 或删除 .kill_switch")
            return

    # ── 夜间禁投检查（8/15 封号复盘后新增：21点后不投，代码强制）──
    if not args.dry_run:
        _safety_start = get_safety(cfg)
        if in_night_window(_safety_start):
            print(f"🌙 当前处于夜间禁投时段 ({_safety_start['night_ban_start']}:00-{_safety_start['night_ban_end']}:00)")
            print(f"   为避免封号风险，投递脚本已拒绝启动。请在白天时段运行。")
            return

    sleep_status = get_sleep_status()
    if sleep_status:
        print(f"💤 睡眠模式: {sleep_status}")
        print(f"   恢复: python3 boss_apply.py --resume")

    # 确定城市列表
    if args.daily or (not args.city and not args.cities):
        cities = args.cities.split(",") if args.cities else _resolve_cities(cfg)
    elif args.cities:
        cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    elif args.city:
        cities = [args.city]
    else:
        cities = _resolve_cities(cfg)

    # 确定搜索词列表
    if args.daily or (not args.job and not args.jobs):
        keywords = args.jobs.split(",") if args.jobs else _resolve_keywords(cfg)
    elif args.jobs:
        keywords = [k.strip() for k in args.jobs.split(",") if k.strip()]
    elif args.job:
        keywords = [args.job]
    else:
        keywords = _resolve_keywords(cfg)

    count = args.count or cfg.get("default_count", 15)
    min_score = args.min_score if args.min_score is not None else cfg.get("min_score", 30)

    # ── --dry-run: 演练模式，只输出计划，绝不连接浏览器/投递 ──
    if args.dry_run:
        print(f"""
╔══════════════════════════════════════╗
║  🧪 DRY-RUN 演练模式（不投递）        ║
╠══════════════════════════════════════╣
║  城市: {', '.join(cities)}         ║
║  搜索: {', '.join(keywords)}        ║
║  每任务上限: {count} 份              ║
║  最低评分: {min_score}               ║
║  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}           ║
╚══════════════════════════════════════╝
""")
        plan = {"mode": "dry-run", "timestamp": datetime.now().isoformat(),
                "cities": cities, "keywords": keywords,
                "count": count, "min_score": min_score,
                "warning": "此模式不会投递任何岗位，仅验证筛选配置"}
        out = Path(__file__).parent / "dry_run_plan.json"
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
        print(f"✅ 演练计划已生成: {out}")
        print("   （未连接浏览器、未搜索、未投递——如需验证真实搜索请手动检查筛选规则）")
        return

    _safety_hdr = get_safety(cfg)
    _mode_hdr = "恢复期(降量)" if is_recovery_active() else "正常期"
    _daily_hdr = _safety_hdr["recovery_daily_cap"] if is_recovery_active() else _safety_hdr["normal_daily_cap"]
    print(f"""
╔══════════════════════════════════════╗
║  🤖 Job Hunter v2 — Boss直聘        ║
╠══════════════════════════════════════╣
║  城市: {', '.join(cities)}         ║
║  搜索: {', '.join(keywords)}        ║
║  每任务上限: {count} 份              ║
║  最低评分: {min_score}               ║
║  安全模式: {_mode_hdr} | 日≤{_daily_hdr} 时≤{_safety_hdr['hourly_cap']} ║
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
    consecutive_failures = 0  # 熔断器：连续失败计数
    _safety = get_safety(cfg)
    # ⚠️ 注意: --count 是"每城市×每关键词上限"，不是总量！
    # 真实总量 = count × 城市数 × 关键词数（61词×9城=549 格）
    # 因此必须用"日志中今日已投总数"做跨进程硬熔断，不依赖 count 参数
    DAILY_LIMIT = _safety["recovery_daily_cap"] if is_recovery_active() else _safety["normal_daily_cap"]  # 每日硬上限（2026-08-16: 由 safety 配置决定）
    SAFETY_DAILY_CAP = DAILY_LIMIT   # 跨进程：今日已投达到此值 → 无论 count 多少都停（防再次超投封号）
    HOURLY_CAP = _safety["hourly_cap"]  # 单小时已投达到此值 → 休息 30 分钟再继续
    CIRCUIT_BREAK_THRESHOLD = 3  # 连续 3 次失败 → 自动熔断（S2 级防护）

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
            # ── 夜间禁投实时检查（跨过 22:00 就停，不恋战）──
            if in_night_window(_safety):
                print(f"\n  🌙 已进入夜间禁投时段 ({_safety['night_ban_start']}:00-{_safety['night_ban_end']}:00)，停止今天的投递")
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
                consecutive_failures += 1
                print(f"  ❌ 错误: {str(e)[:80]}")
            else:
                # 本轮成功执行（无论投出几份），重置连续失败计数
                if f == 0:
                    consecutive_failures = 0

            # ── 熔断器：连续失败达到阈值 → risk_triggered 层级（异常，关全局开关）──
            if consecutive_failures >= CIRCUIT_BREAK_THRESHOLD:
                reason = f"连续 {consecutive_failures} 次失败自动熔断"
                print(f"\n  🛑 [risk_triggered] {reason} — 停止投递，写入 kill switch + 暂停锁")
                kill_switch_off(reason)
                pause(reason)
                break

            # ── 跨进程每日硬熔断 → daily_limit_reached 层级（正常结束，不改 kill switch）──
            # 2026-08-16 A5.1：跑满上限是"正常状态机结束"，不是异常。只结束当天任务，
            # 不写 kill switch / 暂停锁，明天自动恢复。只有连续失败/风控信号才关全局开关。
            today_total = count_applied_since(datetime.now().strftime("%Y-%m-%dT00:00:00"))
            if today_total >= SAFETY_DAILY_CAP:
                print(f"\n  🔚 [daily_limit_reached] 今日已投 {today_total} 份 ≥ 上限 {SAFETY_DAILY_CAP}（跨进程统计）")
                print(f"    正常结束今日任务 — kill switch 未动，明日自动恢复")
                break

            hour_count = count_applied_since(datetime.now().strftime("%Y-%m-%dT%H:00:00"))
            # ── 单小时熔断 → hour_limit_reached 层级（暂停当前任务，不改 kill switch）──
            # 2026-08-16 A5.1：分段 sleep，可被 Ctrl+C/SIGTERM 提前打断，不阻塞退出
            if hour_count >= HOURLY_CAP:
                rest = 30 * 60
                print(f"\n  🕐 [hour_limit_reached] 本小时已投 {hour_count} 份 ≥ {HOURLY_CAP}，暂停 {rest//60} 分钟防风控（kill switch 未动）")
                for _ in range(rest // 5):
                    if SHOULD_STOP:
                        print(f"  ⏸️  收到停止信号，提前结束休息")
                        break
                    time.sleep(5)

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

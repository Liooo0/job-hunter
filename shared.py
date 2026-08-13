"""各平台投递脚本公共模块 — 配置、评分、日志、报告"""

import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# 专用 Chrome 用户目录（投简历用，独立目录，不影响你的日常 Chrome）
JOB_HUNTER_CHROME = os.path.expanduser("~/job-hunter-chrome")


# ═══════════════════════════════════════════════════════════════
#  Kill Switch — 全局开关（2026-08-13 新增，S1 级）
#  一个状态文件控制所有自动任务是否允许执行。
#  位置: ~/projects/job-hunter/.kill_switch
#  内容: {"enabled": false, "reason": "...", "set_at": "..."}
#  任何脚本执行写操作（投递/回复/归档）前必须调 kill_switch_check()。
# ═══════════════════════════════════════════════════════════════
KILL_SWITCH_FILE = Path(__file__).parent / ".kill_switch"


def kill_switch_check() -> tuple[bool, str]:
    """返回 (是否允许执行, 原因)。enabled=false 时所有写操作应停止。"""
    try:
        if KILL_SWITCH_FILE.exists():
            d = json.loads(KILL_SWITCH_FILE.read_text(encoding="utf-8"))
            if not d.get("enabled", True):
                return False, d.get("reason", "kill switch 关闭")
    except Exception:
        pass
    return True, ""


def kill_switch_off(reason: str):
    """关闭 kill switch（禁止所有写操作）。"""
    KILL_SWITCH_FILE.write_text(
        json.dumps({"enabled": False, "reason": reason,
                    "set_at": datetime.now().isoformat()}, ensure_ascii=False)
    )
    print(f"🛑 KILL SWITCH 已关闭: {reason}")


def kill_switch_on():
    """恢复 kill switch（允许写操作）。"""
    KILL_SWITCH_FILE.write_text(
        json.dumps({"enabled": True, "reason": "",
                    "set_at": datetime.now().isoformat()}, ensure_ascii=False)
    )
    print("✅ KILL SWITCH 已恢复")


def kill_switch_status() -> str:
    try:
        d = json.loads(KILL_SWITCH_FILE.read_text(encoding="utf-8"))
        return f"enabled={d.get('enabled', True)}, reason={d.get('reason', '')}"
    except Exception:
        return "enabled=True (未设置)"


def get_chrome_opts(port: int = 9224):
    """返回 ChromiumOptions — 专用独立目录 + 独立端口"""
    from DrissionPage import ChromiumOptions
    opts = ChromiumOptions()
    opts.set_local_port(port)
    opts.set_user_data_path(JOB_HUNTER_CHROME)
    # 不指定 browser_path — DrissionPage 会自动查找系统 Chrome
    opts.set_argument("--no-first-run")
    opts.set_argument("--no-default-browser-check")
    opts.set_argument("--window-name=🔴投递-勿关")
    return opts

DEFAULT_CONFIG = {
    "resume_path": "",
    "greeting": "您好，我对贵司岗位非常感兴趣，期待进一步沟通！",
    "skills": [],
    "target_roles": [],
    "exclude_keywords": ["总监", "架构师", "首席", "VP", "P8", "P7"],
    "boost_keywords": ["llm", "大模型", "agent", "rag", "gpt", "ai产品", "人工智能"],
    "min_score": 60,
    "default_count": 20,
    "target_cities": [],
    "search_keywords": [],
    "schedule": {"times": ["09:00", "15:30"], "jitter_minutes": 10},
    "report_dir": "data/reports",
}


def load_config(skill_dir: Optional[Path] = None) -> dict:
    """读取用户配置。优先 config.json，缺省字段用 DEFAULT_CONFIG 补齐。"""
    skill_dir = skill_dir or Path(__file__).parent
    cfg_file = skill_dir / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    if cfg_file.exists():
        cfg.update(json.loads(cfg_file.read_text(encoding="utf-8")))
    return cfg


def load_log(log_file: Path) -> dict:
    if log_file.exists():
        return json.loads(log_file.read_text())
    return {"applied": [], "skipped": [], "failed": []}


def save_log(log: dict, log_file: Path):
    log_file.write_text(json.dumps(log, ensure_ascii=False, indent=2))
    # Flush to disk immediately (prevent data loss on kill)
    try:
        import os; os.fsync(log_file.open().fileno())
    except:
        pass


def score_jd(title: str, desc: str, config: Optional[dict] = None) -> tuple[int, str]:
    """基于关键词的快速评分。

    config 字段：
      - exclude_keywords: 命中任一则归零
      - target_roles:     命中标题 +30
      - skills:           每命中一个 JD +5，封顶 +30
      - boost_keywords:   JD 命中任一 +10
    """
    cfg = config or DEFAULT_CONFIG
    title_lower = title.lower()
    combined = title_lower + " " + desc.lower()

    # must_contain 检查（标题或JD正文中至少命中一个）
    must = cfg.get("must_contain", [])
    if must:
        if not any(kw.lower() in combined for kw in must):
            return 0, f"缺少必须关键词: {must[0]}..."

    # 排除词只匹配标题，不扫JD正文（避免"网约车"出现在公司描述里就误杀）
    # 注意: "实习/实习生" 跳过标题排除——高薪实习(日薪≥300)由下方放行逻辑处理，
    #       低薪实习由 smart_filter 薪资规则拦截（2026-08-12 修复回归）
    for kw in cfg.get("exclude_keywords", []):
        if kw.lower() in title_lower:
            if kw in ("实习", "实习生"):
                continue
            return 0, f"标题包含排除词: {kw}"

    score = 0
    hits = []

    for role in cfg.get("target_roles", []):
        if role.lower() in title_lower:
            score += 30
            hits.append(f"目标岗位:{role}")
            break

    if any(kw in combined for kw in ["实习", "校招", "应届", "intern"]):
        score += 30
        hits.append("接受实习/应届")

    skill_matches = [s for s in cfg.get("skills", []) if s.lower() in combined]
    if skill_matches:
        bonus = min(len(skill_matches) * 5, 30)
        score += bonus
        hits.append(f"技能匹配:{'/'.join(skill_matches[:3])}")

    for kw in cfg.get("boost_keywords", []):
        if kw.lower() in combined:
            score += 10
            hits.append(kw)
            break

    return min(score, 100), "、".join(hits) if hits else "无匹配"


def smart_filter(company: str, title: str, desc: str, salary: str, score: int, config: Optional[dict] = None, city: str = "深圳") -> tuple[int, str]:
    """基于公司规模/性质/薪资/技术含量的智能过滤。

    返回 (adjusted_score, reason)。
    - adjusted_score 归零 = 过滤抛弃
    - 正值 = 保留下调后分数
    - score 不变 = 正常通过
    """
    cfg = config or DEFAULT_CONFIG
    company_lower = (company or "").lower()
    title_lower = title.lower()
    combined = title_lower + " " + (desc or "").lower()

    # ── 公司名关键词排除（如"科技"类公司） ──
    for kw in cfg.get("exclude_company_keywords", []):
        if kw.lower() in company_lower:
            return 0, f"公司名含「{kw}」→排除"

    # ── 识别外包公司（驻场类，但可能对接好项目） ──
    KNOWN_OUTSOURCING = [
        "中软", "软通动力", "博彦", "法本", "纬致", "东信创智",
        "中科创达", "润和", "信必优", "微创", "文思海辉",
        "外企德科", "亿达", "京北方", "汉克时代", "柯莱特",
        "易诚高科", "海橘", "华苏", "诚迈", "拓保", "神州信息",
    ]
    # ── 高价值项目关键词（外包+这类项目=优质跳板） ──
    BOOST_PROJECTS = [
        "特斯拉", "蔚来", "理想", "小鹏", "比亚迪", "吉利",
        "大众", "博世", "主机厂", "智驾", "adas", "autopilot",
        "fsd", "noa", "l4", "自动驾驶", "车联网",
    ]

    is_outsourcing = any(kw in company_lower for kw in KNOWN_OUTSOURCING)

    # ── 解析薪资下限（K/月） ──
    # 兼容: "12-20K"、"1.2-2万"、"15-25K·13薪"、"3.5-4万"
    salary_low = 0
    try:
        s = salary.replace(" ", "").replace(",", "")
        if "万" in s:
            # 51job/智联格式: "1.2-2万"
            raw = s.split("-")[0].replace("万", "")
            salary_low = float(raw) * 10  # 转K
        elif "k" in s.lower():
            raw = s.lower().split("-")[0].split("k")[0]
            salary_low = float(raw)
        elif "元/天" in s or "元/日" in s:
            # 日薪格式: "200-300元/天"
            raw = s.split("-")[0].split("元")[0]
            salary_low = float(raw) * 22 / 1000  # 月薪≈日薪*22天/1000
    except Exception:
        pass

    # ── 高薪实习放行：标题含"实习"且日薪≥300 → 恢复分数 ──
    # 日薪来源：salary 字段（元/天格式）或 JD 正文（如"560-600元/天"）
    if "实习" in title_lower:
        daily_salary = 0
        try:
            if "元/天" in salary or "元/日" in salary:
                daily_salary = float(salary.split("-")[0].split("元")[0])
            else:
                # 从正文提取日薪（如 "实习560-600元/天"）
                import re as _re
                m = _re.search(r"(\d+(?:\.\d+)?)\s*[-~—]\s*(\d+(?:\.\d+)?)\s*元/(?:天|日)", desc_lower or "")
                if m:
                    daily_salary = float(m.group(1))
                elif _re.search(r"(\d+(?:\.\d+)?)\s*元/(?:天|日)", desc_lower or ""):
                    daily_salary = float(_re.search(r"(\d+(?:\.\d+)?)\s*元/(?:天|日)", desc_lower or "").group(1))
        except Exception:
            pass
        if daily_salary >= 300:
            score = max(score, 60)
            reason_parts.append(f"高薪实习({daily_salary:.0f}元/天)")

    # ── 技术含量检测 ──
    TECH_KEYWORDS = [
        "linux", "can", "canal", "canoe", "log", "日志", "测试用例",
        "python", "shell", "grep", "awk", "tail", "jira", "git",
        "传感器", "sensor", "摄像头", "雷达", "lidar", "毫米波",
        "诊断", "diagnos", "hil", "sil", "台架", "仿真",
        "标定", "calibration", "功能安全", "aspice", "iso",
    ]
    tech_score = sum(1 for kw in TECH_KEYWORDS if kw in combined)

    # ── 司机类关键词（没有技术含量=纯开车） ──
    DRIVER_ONLY = [
        "自动挡", "接送", "司机", "代驾", "网约车", "货运",
        "押运", "配送", "日结", "临时", "驾驶员", "c1",
    ]
    is_driver_focused = any(kw in title_lower for kw in DRIVER_ONLY)

    reason_parts = []

    # Rule 0: 公司名含排除词 → 直接过滤
    if any(kw in company_lower for kw in cfg.get("exclude_company_keywords", [])):
        return 0, "公司名排除→过滤"

    # Rule 0.5: 身体条件/高级Python框架 在JD正文中 → 直接过滤
    for kw in cfg.get("body_exclude_keywords", []):
        if kw.lower() in combined:
            return 0, f"JD含排除词'{kw}'→过滤"

    # Rule 1: 薪资过滤 (v1.0)
    salary_filter = cfg.get("salary_filter", {})
    home_cities = salary_filter.get("home_cities", ["深圳"])
    if salary_low > 0:
        if city in home_cities:
            if salary_low < salary_filter.get("home_min_accept", 8):
                return 0, f"薪资过低({salary_low}K<{salary_filter['home_min_accept']}K)→过滤"
        else:
            if salary_low < salary_filter.get("away_min_accept", 10):
                return 0, f"外地低薪({salary_low}K<{salary_filter['away_min_accept']}K)→过滤"

    # Rule 2: 小微劳务中介 — 外包+薪资极低+无技术含量 → 直接过滤
    if is_outsourcing and salary_low < 6 and tech_score < 2:
        return 0, "外包低薪无技术→过滤"
    if salary_low > 0 and salary_low < 5 and tech_score == 0:
        return 0, "极低薪资无技术→过滤"

    # Rule 3: 纯司机岗 — 技术关键词=0 + 司机类标题 → 过滤
    if is_driver_focused and tech_score == 0:
        return 0, "纯司机岗无技术→过滤"

    # Rule 4: 工人/操作工类岗位 → 过滤
    if any(kw in title_lower for kw in ["学徒", "师傅", "组装", "装配", "操作工", "普工"]):
        return 0, "非测试类工人岗→过滤"

    # Rule 5: 外包+高价值项目 → 加分（职业跳板）
    if is_outsourcing and any(kw in combined for kw in BOOST_PROJECTS):
        boost = 10
        reason_parts.append("高价值外包跳板+" + str(boost))
        return score + boost, "、".join(reason_parts) if reason_parts else ""

    # Rule 6: 技术含量很低（<=1个技术关键词）且薪资<8K → 降级
    # 豁免：B级方向岗（实施/运营/顾问/知识库/工作流）——运营类岗位天然技术词少但方向对
    if tech_score <= 1 and salary_low > 0 and salary_low < 8:
        b_direction = any(kw in title_lower for kw in ["实施", "运营", "顾问", "知识库", "工作流", "解决方案", "技术支持", "培训师", "训练师", "助理"])
        if b_direction:
            reason_parts.append(f"B级方向岗豁免Rule6({tech_score}技/{salary_low}K)")
        elif score > 0:
            return max(0, score - 30), f"低技术低薪资({tech_score}技/{salary_low}K)"
        else:
            return 0, f"低技术低薪资({tech_score}技/{salary_low}K)→过滤"

    return score, ""


# ═══════════════════════════════════════════════════════════════
#  日志汇总 & 报告
# ═══════════════════════════════════════════════════════════════

def list_log_files(skill_dir: Optional[Path] = None) -> list[Path]:
    """返回 skill 目录下所有 *-log.json 文件路径。"""
    skill_dir = skill_dir or Path(__file__).parent
    return sorted(skill_dir.glob("*-log.json"))


def merge_logs(skill_dir: Optional[Path] = None) -> dict:
    """合并所有城市/平台的 log，汇总 applied/skipped/failed。"""
    merged = {"applied": [], "skipped": [], "failed": []}
    for f in list_log_files(skill_dir):
        log = load_log(f)
        for key in merged:
            merged[key].extend(log.get(key, []))
    return merged


def get_today_new(skill_dir: Optional[Path] = None) -> dict:
    """从所有 log 中提取今天新增的投递（按 job title 去重）。"""
    skill_dir = skill_dir or Path(__file__).parent
    today_str = date.today().isoformat()
    seen = set()
    result = {"applied": [], "total_seen": 0, "total_applied": 0, "total_skipped": 0}
    for f in list_log_files(skill_dir):
        log = load_log(f)
        result["total_applied"] += len(log.get("applied", []))
        result["total_skipped"] += len(log.get("skipped", []))
        for entry in log.get("applied", []):
            key = entry.get("job", "") + entry.get("company", "")
            if key not in seen:
                seen.add(key)
                entry["_log_file"] = f.stem
                result["applied"].append(entry)
    result["total_seen"] = result["total_applied"] + result["total_skipped"]
    return result


def recent_activity(skill_dir: Optional[Path] = None, days: int = 7) -> list[dict]:
    """按日期统计最近 N 天的投递量（基于日志文件的 mtime）。"""
    skill_dir = skill_dir or Path(__file__).parent
    cutoff = datetime.now() - timedelta(days=days)
    daily: dict[str, dict] = {}

    for f in list_log_files(skill_dir):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime < cutoff:
            continue
        day = mtime.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"date": day, "applied": 0, "skipped": 0, "failed": 0}
        log = load_log(f)
        daily[day]["applied"] += len(log.get("applied", []))
        daily[day]["skipped"] += len(log.get("skipped", []))
        daily[day]["failed"] += len(log.get("failed", []))

    return sorted(daily.values(), key=lambda d: d["date"])


def format_salary(raw: str) -> str:
    """把 Boss 的 PUA 字体编码薪资格式化为可读字符串。

    传入的 raw 可能是：
    - 已解码的正常字符串 "15-20K·13薪"
    - 仍然含 PUA 字符的原始字符串

    如果是后者，尝试用 PUA 映射解码。
    """
    if not raw:
        return ""
    # PUA 数字映射（Boss直聘自定义字体 E600-E609 = 0-9）
    PUA_MAP = {
        "": "0", "": "1", "": "2", "": "3", "": "4",
        "": "5", "": "6", "": "7", "": "8", "": "9",
    }
    result = raw
    for pua, digit in PUA_MAP.items():
        result = result.replace(pua, digit)
    return result


def ensure_dir(p: Path):
    """确保目录存在。"""
    p.mkdir(parents=True, exist_ok=True)

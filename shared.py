"""各平台投递脚本公共模块 — 配置、评分、日志、报告"""

import json
import os
import re
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


def _fill_fallbacks(cfg: dict) -> dict:
    """用内置默认补齐缺失配置块 —— 别人 clone 后没有 config.json 也能跑。

    只补缺：用户显式给出的键一律不覆盖；dict 块做逐键补齐。
    """
    for key, val in FALLBACK_CONFIG.items():
        if key not in cfg or cfg[key] is None:
            cfg[key] = json.loads(json.dumps(val))  # deep copy，防运行时污染默认值
        elif isinstance(val, dict) and isinstance(cfg[key], dict):
            for k2, v2 in val.items():
                cfg[key].setdefault(k2, v2)
    return cfg


def load_config(skill_dir: Optional[Path] = None) -> dict:
    """读取用户配置。优先 config.json，缺省字段用 DEFAULT_CONFIG 补齐；
    再用 FALLBACK_CONFIG 补齐完整功能块（词表/薪资线/岗位池），
    保证 fresh clone（无 config.json）开箱即用。
    """
    skill_dir = skill_dir or Path(__file__).parent
    cfg_file = skill_dir / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    if cfg_file.exists():
        cfg.update(json.loads(cfg_file.read_text(encoding="utf-8")))
    return _fill_fallbacks(cfg)


# 内置兜底配置：与作者实战 config 同构的精简代表版（不含任何个人信息）。
# 新用户以此为起点即可跑通全流程，之后按自己情况改 config.json 覆盖。
FALLBACK_CONFIG = {
    "skills": [
        "Python", "RAG", "Dify", "Agent", "智能体", "知识库", "工作流",
        "LLM", "大模型", "Prompt", "MCP", "Coze", "LangChain", "FastAPI",
    ],
    "boost_keywords": [
        "Agent", "RAG", "Dify", "LLM", "大模型", "MCP", "工作流",
        "知识库", "AI实施", "解决方案", "企业AI落地",
    ],
    "exclude_keywords": [
        "总监", "架构师", "首席", "资深", "P7", "P8", "销售", "电销", "地推",
        "数据标注", "短视频", "短剧", "实习", "实习生", "校招", "应届", "管培生",
    ],
    "body_exclude_keywords": [
        "大小周", "单休", "996", "上六休一", "夜班", "倒班", "长期出差",
        "电销", "面销", "课程顾问", "招生",
    ],
    "salary_filter": {
        "home_cities": ["深圳", "广州", "佛山", "惠州", "珠海", "中山", "东莞"],
        "home_min_accept": 6,
        "home_min_prefer": 10,
        "away_min_accept": 10,
        "away_min_prefer": 12,
    },
    "job_pools": {
        "keywords": {
            "S级-AI实施/解决方案": [
                "AI实施工程师", "AI解决方案工程师", "AI技术支持", "企业AI应用",
                "智能化解决方案", "数字化实施",
            ],
            "S级-AI应用工程师": [
                "AI应用工程师", "LLM应用", "大模型应用", "Agent应用",
                "智能体开发", "RAG", "知识库运营", "Dify", "Coze",
            ],
            "A级-车联网/智能汽车": [
                "车联网测试", "车载测试", "智能座舱", "OTA测试", "ADAS测试",
            ],
            "A级-IT技术支持/数字化": [
                "技术支持", "IT support", "helpdesk", "系统运维",
            ],
            "B级-AI内容运营": ["AI内容运营", "AIGC运营"],
        }
    },
    "city_pools": {
        "city_priority": {
            "深圳": "primary",
            "广州": "secondary", "佛山": "secondary", "惠州": "secondary",
            "上海": "opportunistic", "苏州": "opportunistic", "杭州": "opportunistic",
        }
    },
    "min_score": 10,
}
FALLBACK_JOB_POOLS = FALLBACK_CONFIG["job_pools"]["keywords"]


def load_log(log_file: Path) -> dict:
    if log_file.exists():
        return json.loads(log_file.read_text())
    return {"applied": [], "skipped": [], "failed": []}


# ═══════════════════════════════════════════════════════════════
#  词边界关键词匹配（2026-08-26）
#  修裸子串误报: "AI" 命中 "Maintained"、"Go" 命中 "Google"、"API" 命中 "Rapid"。
#  规则（参考 BossZhiPin_Job_Search 的 job_matcher.py）:
#  - 关键词首/尾是 ASCII 字母数字时，该侧加 (?<![A-Za-z0-9]) / (?![A-Za-z0-9])
#  - 纯符号侧（".NET" 的 "."、"C++" 的 "+"）不加边界
#  - 中文关键词无词边界概念，退化为子串匹配
# ═══════════════════════════════════════════════════════════════
_KW_PATTERN_CACHE = {}


def contains_kw(haystack: str, kw: str) -> bool:
    """词边界关键词匹配。ASCII 词首尾加字母数字边界，中文退化子串；空串安全。"""
    if not haystack or not kw:
        return False
    key = kw.lower()
    pat = _KW_PATTERN_CACHE.get(key)
    if pat is None:
        left = r"(?<![A-Za-z0-9])" if re.match(r"[a-z0-9]", key[:1]) else ""
        right = r"(?![A-Za-z0-9])" if re.match(r"[a-z0-9]", key[-1:]) else ""
        pat = re.compile(left + re.escape(key) + right, re.IGNORECASE)
        _KW_PATTERN_CACHE[key] = pat
    return bool(pat.search(haystack))


# ── 外包公司名单 / 高价值项目词表（原 smart_filter 局部变量提为模块级，供 match_engine 复用）──
KNOWN_OUTSOURCING = [
    "中软", "软通动力", "博彦", "法本", "纬致", "东信创智",
    "中科创达", "润和", "信必优", "微创", "文思海辉",
    "外企德科", "亿达", "京北方", "汉克时代", "柯莱特",
    "易诚高科", "海橘", "华苏", "诚迈", "拓保", "神州信息",
]
BOOST_PROJECTS = [
    "特斯拉", "蔚来", "理想", "小鹏", "比亚迪", "吉利",
    "大众", "博世", "主机厂", "智驾", "adas", "autopilot",
    "fsd", "noa", "l4", "自动驾驶", "车联网",
]

# ── Boss 城市代码（P2-T6 统一：原 boss_apply.py 与 deep_filter.py 各有一份且漂移）──
# 取两份并集：boss_apply 21 城 ⊇ deep_filter 12 城，重叠城市代码逐个核对一致。
# 消费方：boss_apply（搜索 URL）、deep_filter（公司背调搜索）。
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
        if not any(contains_kw(combined, kw) for kw in must):
            return 0, f"缺少必须关键词: {must[0]}..."

    # 排除词只匹配标题，不扫JD正文（避免"网约车"出现在公司描述里就误杀）
    # 注意: "实习/实习生" 跳过标题排除——高薪实习(日薪≥300)由下方放行逻辑处理，
    #       低薪实习由 smart_filter 薪资规则拦截（2026-08-12 修复回归）
    for kw in cfg.get("exclude_keywords", []):
        if contains_kw(title, kw):
            if kw in ("实习", "实习生"):
                continue
            return 0, f"标题包含排除词: {kw}"

    score = 0
    hits = []

    for role in cfg.get("target_roles", []):
        if contains_kw(title, role):
            score += 30
            hits.append(f"目标岗位:{role}")
            break

    if any(contains_kw(combined, kw) for kw in ["实习", "校招", "应届", "intern"]):
        score += 30
        hits.append("接受实习/应届")

    skill_matches = [s for s in cfg.get("skills", []) if contains_kw(combined, s)]
    if skill_matches:
        bonus = min(len(skill_matches) * 5, 30)
        score += bonus
        hits.append(f"技能匹配:{'/'.join(skill_matches[:3])}")

    for kw in cfg.get("boost_keywords", []):
        if contains_kw(combined, kw):
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
    salary = salary or ""
    company_lower = (company or "").lower()
    title_lower = title.lower()
    desc_lower = (desc or "").lower()
    combined = title_lower + " " + desc_lower
    # 原因收集器必须在任何 append 之前初始化（B1：高薪实习分支会先用到）
    reason_parts = []

    # ── 公司名关键词排除（如"科技"类公司） ──
    for kw in cfg.get("exclude_company_keywords", []):
        if contains_kw(company, kw):
            return 0, f"公司名含「{kw}」→排除"

    # KNOWN_OUTSOURCING / BOOST_PROJECTS 已提为模块级（match_engine 复用）

    is_outsourcing = any(contains_kw(company, kw) for kw in KNOWN_OUTSOURCING)

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
    tech_score = sum(1 for kw in TECH_KEYWORDS if contains_kw(combined, kw))

    # ── 司机类关键词（没有技术含量=纯开车） ──
    DRIVER_ONLY = [
        "自动挡", "接送", "司机", "代驾", "网约车", "货运",
        "押运", "配送", "日结", "临时", "驾驶员", "c1",
    ]
    is_driver_focused = any(contains_kw(title, kw) for kw in DRIVER_ONLY)

    # Rule 0: 公司名含排除词 → 直接过滤
    if any(contains_kw(company, kw) for kw in cfg.get("exclude_company_keywords", [])):
        return 0, "公司名排除→过滤"

    # Rule 0.5: 身体条件/高级Python框架 在JD正文中 → 直接过滤
    for kw in cfg.get("body_exclude_keywords", []):
        if contains_kw(combined, kw):
            return 0, f"JD含排除词'{kw}'→过滤"

    # Rule 1: 薪资过滤 (v1.0)
    salary_filter = cfg.get("salary_filter", {})
    home_cities = salary_filter.get("home_cities", ["深圳"])
    if salary_low > 0:
        if city in home_cities:
            home_min = float(salary_filter.get("home_min_accept", 8))
            if salary_low < home_min:
                return 0, f"薪资过低({salary_low}K<{home_min:g}K)→过滤"
        else:
            away_min = float(salary_filter.get("away_min_accept", 10))
            if salary_low < away_min:
                return 0, f"外地低薪({salary_low}K<{away_min:g}K)→过滤"

    # Rule 2: 小微劳务中介 — 外包+薪资极低+无技术含量 → 直接过滤
    if is_outsourcing and salary_low < 6 and tech_score < 2:
        return 0, "外包低薪无技术→过滤"
    if salary_low > 0 and salary_low < 5 and tech_score == 0:
        return 0, "极低薪资无技术→过滤"

    # Rule 3: 纯司机岗 — 技术关键词=0 + 司机类标题 → 过滤
    if is_driver_focused and tech_score == 0:
        return 0, "纯司机岗无技术→过滤"

    # Rule 4: 工人/操作工类岗位 → 过滤
    if any(contains_kw(title, kw) for kw in ["学徒", "师傅", "组装", "装配", "操作工", "普工"]):
        return 0, "非测试类工人岗→过滤"

    # Rule 5: 外包+高价值项目 → 加分（职业跳板）
    if is_outsourcing and any(contains_kw(combined, kw) for kw in BOOST_PROJECTS):
        boost = 10
        reason_parts.append("高价值外包跳板+" + str(boost))
        return score + boost, "、".join(reason_parts) if reason_parts else ""

    # Rule 6: 技术含量很低（<=1个技术关键词）且薪资<8K → 降级
    # 豁免：B级方向岗（实施/运营/顾问/知识库/工作流）——运营类岗位天然技术词少但方向对
    if tech_score <= 1 and salary_low > 0 and salary_low < 8:
        b_direction = any(contains_kw(title, kw) for kw in ["实施", "运营", "顾问", "知识库", "工作流", "解决方案", "技术支持", "培训师", "训练师", "助理"])
        if b_direction:
            reason_parts.append(f"B级方向岗豁免Rule6({tech_score}技/{salary_low}K)")
        elif score > 0:
            return max(0, score - 30), f"低技术低薪资({tech_score}技/{salary_low}K)"
        else:
            return 0, f"低技术低薪资({tech_score}技/{salary_low}K)→过滤"

    return score, "、".join(reason_parts) if reason_parts else ""


# ═══════════════════════════════════════════════════════════════
#  日志汇总 & 报告
# ═══════════════════════════════════════════════════════════════

def list_log_files(skill_dir: Optional[Path] = None) -> list[Path]:
    """返回 skill 目录下所有 *-log.json 文件路径。"""
    skill_dir = skill_dir or Path(__file__).parent
    return sorted(skill_dir.glob("*-log.json"))


def merge_logs(skill_dir: Optional[Path] = None) -> dict:
    """合并所有城市/平台的投递记录，汇总 applied/skipped/failed。

    P0 起单一事实源为 SQLite（store.py）；库为空时回退读旧 JSON（首次自动迁移）。
    """
    from store import ensure_migrated, is_empty, merge_records
    skill_dir = skill_dir or Path(__file__).parent
    ensure_migrated()
    if not is_empty():
        return merge_records()

    merged = {"applied": [], "skipped": [], "failed": []}
    for f in list_log_files(skill_dir):
        log = load_log(f)
        for key in merged:
            merged[key].extend(log.get(key, []))
    return merged


def recent_activity(skill_dir: Optional[Path] = None, days: int = 7) -> list[dict]:
    """按日期统计最近 N 天的投递量（按 entry.time 聚合，不再依赖文件 mtime）。"""
    from store import ensure_migrated, is_empty, recent_activity_days
    skill_dir = skill_dir or Path(__file__).parent
    ensure_migrated()
    if not is_empty():
        return recent_activity_days(days)

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    daily: dict[str, dict] = {}

    for f in list_log_files(skill_dir):
        log = load_log(f)
        for key in ("applied", "skipped", "failed"):
            for e in log.get(key, []):
                day = (e.get("time") or "")[:10]
                if not day or day < cutoff:
                    continue
                if day not in daily:
                    daily[day] = {"date": day, "applied": 0, "skipped": 0, "failed": 0}
                daily[day][key] += 1

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

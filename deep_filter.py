"""深度筛选模块 — 标题党检测 + 公司背调（2026-08-07 新增）

解决 2026-08-07 验证出的三类漏网：
1. 标题党：标题带 AI，正文是传统开发（华勤=Android车载、餐谋团=全栈运维）
2. 公司伪装：单岗位看着行，公司其实是销售/标注外包（奇合创、卓越际联）
3. 实习薪资陷阱：标题写月薪区间，正文实际日薪<300（小和云起 200/天）

设计原则：
- 标题党检测 = 纯本地文本分析，零成本，每次调用
- 公司背调 = 搜索 API 拉公司全部在招岗位，带本地缓存（data/company_profiles.json），
  每公司只查一次；API 失败时降级为"未知"，不误杀
- 风控：背调请求间隔 ≥8s，且只对"即将投递"的岗位做（本地过滤全过之后）
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# 标题党检测（本地）
# ═══════════════════════════════════════════════════════════════

# 标题里出现这些 = 岗位宣称是 AI 岗
AI_TITLE_SIGNALS = [
    "ai应用", "ai 应用", "ai工程师", "ai 工程师", "ai开发", "ai 开发",
    "智能体", "agent", "llm", "大模型", "rag", "aigc", "ai agent",
    "ai训练", "ai 训练", "ai产品", "ai 产品", "ai运营", "ai 运营",
    "ai工作流", "ai 工作流", "prompt", "提示词", "gpt", "coze", "dify",
    "ai自动化", "ai 自动化", "ai解决方案", "ai 解决方案", "数字员工",
    "ai多模态", "ai 多模态", "ai标注", "ai 标注", "ai评测", "ai 评测",
]

# 正文里出现这些 = 传统开发技术栈（非 AI 应用开发）
TRADITIONAL_STACK = [
    "android", "ios", "flutter", "react native", "rn开发", "小程序开发",
    "vue", "react", "css", "html", "javascript", "typescript", "node.js",
    "spring", "springboot", "spring cloud", "golang", "java开发",
    "java 后端", "后端开发", "全栈", "前端开发", "运维", "k8s", "kubernetes",
    "docker部署", "数据库", "mysql", "postgresql", "oracle", "sql server",
    "etl", "hadoop", "spark", "数据仓库", "c++", "c/c++", "c#", ".net",
    "嵌入式", "单片机", "fpga", "驱动开发", "固件",
    "车机", "座舱", "车载", "android framework", "launcher",
    "架构设计", "微服务", "高并发", "性能优化", "中间件",
    # 传统业务系统（评审 2026-08-07 补充）
    "erp", "oa系统", "crm", "电商后台", "交易系统", "订单系统",
    "支付系统", "报表系统", "管理系统开发",
]

# Python 后端 + LLM = AI 应用岗的常见形态，不算传统业务后端。
# 当正文出现这些时，即使有"后端/全栈"字样也不判定为传统开发。
PYTHON_AI_SAFE = [
    "python", "fastapi", "flask", "llm", "大模型", "rag", "agent",
    "langchain", "langgraph", "智能体", "向量", "embedding", "prompt",
    "dify", "coze", "mcp", "openai", "deepseek", "qwen", "api对接",
    "工作流", "ai应用", "知识库",
]

# 正文里出现这些 = 真 AI 应用开发（有这些就说明 AI 含量足）
REAL_AI_STACK = [
    "agent", "智能体", "rag", "检索增强", "向量", "embedding",
    "langchain", "langgraph", "dify", "coze", "扣子", "提示词",
    "prompt", "大模型", "llm", "gpt", "千问", "qwen", "deepseek",
    "glm", "kimi", "文心", "通义", "模型微调", "sft", "lora",
    "ai agent", "工作流", "workflow", "function calling", "工具调用",
    "mcp", "知识库", "ai应用", "aigc", "文生图", "多模态",
]

# 实习日薪提取（正文）
DAILY_RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元/(?:天|日)")

# AI 包装检测：废话词（战略/愿景类）多但无技术词 = 假 AI 岗（可能销售/卖课）
AI_BUZZWORDS = ["赋能", "生态", "愿景", "探索人工智能", "推动ai", "引领", "数字化转",
                "ai+", "ai赋能", "拥抱ai", "人工智能未来"]
AI_TECH_REQUIRED = ["api", "模型", "代码", "工具", "平台", "流程", "系统",
                    "python", "rag", "agent", "llm", "大模型", "prompt", "知识库"]


def detect_clickbait(title: str, desc: str) -> tuple[bool, str]:
    """检测标题党：标题宣称 AI 岗，但正文是传统开发。

    返回 (是否标题党, 原因)。
    规则：标题命中 AI 信号 且（正文传统技术栈≥2 或 传统栈≥1且AI栈==0）
    Python 后端 + LLM/API 对接 = AI 应用岗常见形态，不判传统开发。
    """
    t = (title or "").lower()
    d = (desc or "").lower()

    # 标题没有 AI 信号 → 不是标题党（可能是普通岗，由其他规则处理）
    if not any(sig in t for sig in AI_TITLE_SIGNALS):
        return False, ""

    # Python+LLM 保护：正文出现 AI 安全词时，传统栈命中不计（Python 后端是 AI 岗常态）
    python_ai_protected = any(kw in d for kw in PYTHON_AI_SAFE)
    trad_hits = [kw for kw in TRADITIONAL_STACK if kw in d and not (python_ai_protected and kw in ("后端开发", "全栈"))]
    ai_hits = [kw for kw in REAL_AI_STACK if kw in d]

    # 标题含"实习"时，AI 信号里"ai应用/agent"等可能只是标题蹭词
    is_intern = "实习" in t

    if len(trad_hits) >= 2 and len(ai_hits) == 0:
        return True, f"标题党:标题含AI但正文是传统开发({'/'.join(trad_hits[:3])})"
    if len(trad_hits) >= 1 and len(ai_hits) == 0 and not is_intern:
        return True, f"标题党:标题含AI但正文无AI技术词({trad_hits[0]})"
    # 实习岗放宽：日薪>=300 已由 smart_filter 处理，这里只拦"纯传统栈实习"
    if is_intern and len(trad_hits) >= 3 and len(ai_hits) == 0:
        return True, f"标题党(实习):正文是传统开发({'/'.join(trad_hits[:3])})"

    return False, ""


def extract_daily_rate(desc: str, salary: str) -> Optional[float]:
    """从 JD 正文提取日薪（元/天）。标题薪资字段是月薪区间时正文可能写日薪。"""
    for m in DAILY_RATE_RE.finditer(desc or ""):
        return float(m.group(1))
    return None


def detect_salary_trap(title: str, desc: str, salary: str) -> tuple[bool, str]:
    """实习薪资陷阱：标题显示月薪区间（如 10-15K），正文实际日薪<300。"""
    if "实习" not in (title or ""):
        return False, ""
    daily = extract_daily_rate(desc, salary)
    if daily is not None and daily < 300:
        return True, f"实习薪资陷阱:正文日薪{daily:.0f}元/天<300"
    return False, ""


def detect_ai_washing(title: str, desc: str) -> tuple[bool, str]:
    """AI 包装检测：JD 大量"赋能/生态/愿景"废话但无技术词 = 假 AI 岗。

    规则：废话词 ≥2 且 技术词 == 0 → 包装岗（可能销售/卖课/公关）
    """
    d = (desc or "").lower()
    buzz_hits = [kw for kw in AI_BUZZWORDS if kw in d]
    tech_hits = [kw for kw in AI_TECH_REQUIRED if kw in d]
    if len(buzz_hits) >= 2 and len(tech_hits) == 0:
        return True, f"AI包装:废话词({'/'.join(buzz_hits[:3])})但无技术词"
    return False, ""


# ═══════════════════════════════════════════════════════════════
# 公司背调（搜索 API + 本地缓存）
# ═══════════════════════════════════════════════════════════════

CACHE_FILE = Path(__file__).parent / "data" / "company_profiles.json"

# 公司性质判定关键词
SALES_JOB_WORDS = ["销售", "推销", "电话销售", "客户顾问", "业务员", "市场专员",
                   "推广", "地推", "招商", "渠道", "商务拓展", "bd", "导购",
                   "客服", "接线", "电销"]
ANNOTATION_JOB_WORDS = ["标注", "评测", "数据标注", "sft", "rlhf", "rm训练",
                        "训练师", "数据清洗", "打标", "审核", "内容安全",
                        "ai训练", "大模型训练", "标注员"]
REAL_AI_JOB_WORDS = ["ai应用", "agent", "智能体", "rag", "大模型应用", "llm应用",
                     "dify", "coze", "工作流", "ai产品", "ai运营", "prompt",
                     "ai工程师", "ai开发", "aigc", "多模态", "数字员工"]
# 公司名风险词：人力中介/外包/培训公司招的"AI"岗大概率是代招/卖课/驻场
RISK_COMPANY_WORDS = ["人力资源", "劳务派遣", "人才服务", "外包", "咨询", "培训",
                      "教育科技", "信息科技服务", "技术服务有限", "派遣"]
# 公司名出现这些词且岗位是 AI 类 → 高风险降级
RISK_COMPANY_STRONG = ["人力资源", "劳务", "派遣", "代招", "猎头"]

CITY_CODES = {
    "深圳": "101280600", "广州": "101280100", "北京": "101010100",
    "上海": "101020100", "杭州": "101210100", "成都": "101270100",
    "武汉": "101200100", "南京": "101190100", "东莞": "101281600",
    "西安": "101110100", "长沙": "101250100", "重庆": "101040100",
}


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _fetch_company_jobs(company: str, city: str) -> Optional[list]:
    """页面内 fetch 搜索 API 拉公司全部在招岗位。失败返回 None（降级为未知）。"""
    code = CITY_CODES.get(city, "101280600")
    # 公司名太长时截断（搜索词太长命中率低）
    query = company[:8]
    js = f"""
    (async () => {{
      try {{
        const r = await fetch('/wapi/zpgeek/search/joblist.json?scene=1&query={query}&city={code}&page=1&pageSize=15', {{
          headers: {{'accept': 'application/json'}}
        }});
        const d = await r.json();
        const list = (d.zpData && d.zpData.jobList) || [];
        return JSON.stringify(list.map(j => ({{name: j.jobName, brand: j.brandName}})));
      }} catch(e) {{ return 'ERR:' + e.message; }}
    }})()
    """
    # 需要 tab 上下文执行，由调用方提供
    raise NotImplementedError("由 caller 注入执行器")


def profile_company(jobs: list) -> dict:
    """根据公司全部在招岗位列表，判定公司性质。

    返回 {kind, sales_n, annot_n, ai_n, total, jobs}
    kind: 'sales' | 'annotation' | 'ok' | 'unknown'
    """
    total = len(jobs)
    if total == 0:
        return {"kind": "unknown", "total": 0, "jobs": []}
    sales_n = sum(1 for j in jobs if any(w in j.get("name", "") for w in SALES_JOB_WORDS))
    annot_n = sum(1 for j in jobs if any(w in j.get("name", "") for w in ANNOTATION_JOB_WORDS))
    ai_n = sum(1 for j in jobs if any(w in j.get("name", "").lower() for w in REAL_AI_JOB_WORDS))

    # 销售岗占多数 → 销售公司
    if sales_n >= 3 and sales_n / total >= 0.4:
        return {"kind": "sales", "sales_n": sales_n, "annot_n": annot_n,
                "ai_n": ai_n, "total": total, "jobs": jobs[:8]}
    # 标注/评测岗占多数 → 标注外包
    if annot_n >= 2 and annot_n / total >= 0.4:
        return {"kind": "annotation", "sales_n": sales_n, "annot_n": annot_n,
                "ai_n": ai_n, "total": total, "jobs": jobs[:8]}
    return {"kind": "ok", "sales_n": sales_n, "annot_n": annot_n,
            "ai_n": ai_n, "total": total, "jobs": jobs[:8]}


# ═══════════════════════════════════════════════════════════════
# 组合入口
# ═══════════════════════════════════════════════════════════════

def deep_filter(company: str, title: str, desc: str, salary: str,
                score: int, profile: Optional[dict] = None) -> tuple[int, str]:
    """深度筛选总入口。

    profile: 公司画像（来自背调），None = 未背调（跳过公司规则）
    返回 (adjusted_score, reason)。score 归零 = 过滤。
    """
    # 1. 标题党检测（本地）
    clickbait, reason = detect_clickbait(title, desc)
    if clickbait:
        return 0, reason

    # 2. 实习薪资陷阱（本地）
    trap, reason = detect_salary_trap(title, desc, salary)
    if trap:
        return 0, reason

    # 2.5 AI 包装检测（本地）：赋能/生态废话多但无技术词
    wash, reason = detect_ai_washing(title, desc)
    if wash:
        return 0, reason

    # 3. 公司性质（需背调结果）
    if profile:
        kind = profile.get("kind")
        if kind == "sales":
            return 0, f"公司背调:销售公司({profile.get('sales_n')}/{profile.get('total')}销售岗)"
        if kind == "annotation":
            return 0, f"公司背调:标注外包({profile.get('annot_n')}/{profile.get('total')}标注岗)"

    # 4. 公司名风险词（本地，无需背调）：人力/劳务/派遣/代招 → 直接弃
    comp_lower = (company or "").lower()
    for kw in RISK_COMPANY_STRONG:
        if kw in comp_lower:
            return 0, f"公司名含「{kw}」→人力中介/代招风险"

    return score, ""


def run_company_background_check(company: str, city: str, eval_js_fn) -> dict:
    """执行公司背调：查缓存 → 未缓存则调 API → 保存缓存。

    eval_js_fn: 在浏览器 tab 上下文执行 JS 的函数（页面 fetch 需要 cookie）
    返回公司画像 dict。API 失败 → 缓存 'unknown' 短时结果，避免反复请求。
    """
    cache = _load_cache()
    key = f"{company}|{city}"
    if key in cache:
        return cache[key]["profile"]

    try:
        jobs_raw = eval_js_fn(company, city)
        jobs = []
        if jobs_raw and not jobs_raw.startswith("ERR:"):
            data = json.loads(jobs_raw)
            jobs = [j for j in data if j.get("brand") == company or not j.get("brand")]
            # 搜索可能返回多家公司，只保留目标公司
            jobs = [j for j in data if company[:4] in j.get("brand", "")] or data
        profile = profile_company(jobs)
    except Exception:
        profile = {"kind": "unknown", "total": 0, "jobs": []}

    cache[key] = {"profile": profile, "time": datetime.now().isoformat()}
    _save_cache(cache)
    return profile

#!/usr/bin/env python3
"""mail_claim_lint.py — 对外邮件「声称 vs 实物」硬检查（只读）

事故背景（2026-10-22）：一天发 11 封直联邮件，两封的作品证据链错了——
一封声称「我是 Dify 的实际使用者」，附的却是 Flask 手写的 renovation-bot；
一封讲商品上新监控，链接贴成了求职数据看板。两封都已发出，收不回。

四层检查（official_mail_apply.py --send 发送前强制调用）：
    1) 平台/框架名（Dify/Coze/Chroma/LangChain…）必须在条目 sources 声明的
       某个实物里留下痕迹 —— 公开仓库、私有项目目录、或「云端可演示」声明。
    2) 数字（627 / 4.7 万 / 100+ / 181…）必须能在所附仓库 README 或事实清单里找到。
    3) 禁用词表（data/mail_denylist.json）命中即 FAIL —— 拦「屏幕级」「长文档」
       「一个仓库」这类范围夸大。
    4) 正文业务域与所附实物主题必须至少有一个交集（抓贴错链接）。

边界规则：只把 ASCII 字母数字下划线当作词的一部分，汉字不算。
「用Dify搭」必须命中，「modify」不许命中（grep -w 与 Python \\b 都会判错）。

用法：
    PYTHONPATH="" /usr/bin/python3 scripts/mail_claim_lint.py
    PYTHONPATH="" /usr/bin/python3 scripts/mail_claim_lint.py --ids M123 M456
    PYTHONPATH="" /usr/bin/python3 scripts/mail_claim_lint.py --sent-today
    PYTHONPATH="" /usr/bin/python3 scripts/mail_claim_lint.py --json

退出码：0 = 通过；1 = 有 FAIL。
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "data" / "mail_queue"
DENYLIST = ROOT / "data" / "mail_denylist.json"
FACTS = ROOT / "data" / "mail_facts.md"

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build",
             "__pycache__", ".pytest_cache", "site-packages", ".next", "target"}
TEXT_EXT = (".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".html",
            ".js", ".ts", ".cfg", ".ini", ".env")

PLATFORMS = ["Dify", "Coze", "LangChain", "LangGraph", "LlamaIndex", "RAGFlow",
             "FastGPT", "Chroma", "ChromaDB", "Milvus", "FAISS", "Pinecone",
             "Weaviate", "Qdrant", "BGE", "Ollama", "vLLM", "n8n", "Zapier",
             "DrissionPage", "Playwright", "Selenium", "pywebview", "FastAPI",
             "Flask", "Streamlit", "Gradio", "Three.js", "PyInstaller"]

DOMAINS = {
    "求职": ["求职", "投递", "岗位", "招聘", "简历", "面试"],
    "电商零售": ["商品", "SKU", "上新", "竞品", "电商", "零售", "选品", "球鞋"],
    "客服线索": ["客服", "咨询", "问答", "知识库", "线索", "留资", "意向"],
    "工程装修": ["工程", "装修", "BIM", "施工", "改造", "现场"],
    "模型评测": ["评测", "选型", "对战", "延迟", "模型对比", "Elo"],
    "租赁台账": ["租客", "租户", "收租", "抄表", "台账", "账单", "房"],
    "流程自动化": ["自动化", "状态机", "浏览器", "驱动", "CDP", "流程"],
}


def token_rx(tok):
    """ASCII-only 边界：汉字紧贴不漏，modify 里不误报。"""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])", re.I)


def load_denylist():
    if not DENYLIST.exists():
        return []
    try:
        d = json.loads(DENYLIST.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, dict):
        d = d.get("words", [])
    return [w for w in d if isinstance(w, str) and w.strip()]


def repo_dir(repo):
    cands = [HOME / "projects" / repo, HOME / "dev" / repo, HOME / repo,
             HOME / "projects" / repo.replace("-", "_")]
    if repo == "liodesktop":
        cands.insert(0, HOME / "projects" / "lio-desktop")
    if repo == "Liooo0":
        cands.insert(0, HOME / "projects" / "Liooo0")
    for c in cands:
        if c.is_dir():
            return c
    return None


def grep_token(root, tok):
    """目录里是否有该 token 的整词痕迹（ASCII 边界，跳过依赖/构建目录）。"""
    rx = token_rx(tok)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                continue
            if not fn.endswith(TEXT_EXT):
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8",
                          errors="ignore") as fh:
                    if rx.search(fh.read(400000)):
                        return True
            except Exception:
                continue
    return False


def extract_repos(text):
    return sorted(set(re.findall(r"github\.com/Liooo0/([\w.\-]+)", text)))


def domain_groups(text):
    low = text.lower()
    return {g for g, kws in DOMAINS.items() if any(k.lower() in low for k in kws)}


def readme_text(root):
    for name in ("README.md", "readme.md", "README.MD", "README"):
        f = Path(root) / name
        if f.exists():
            try:
                return f.read_text(encoding="utf-8", errors="ignore")[:12000]
            except Exception:
                pass
    return ""


def norm_numbers(text):
    """抽数字并归一化（4.7 万 → 47000，627 → 627）。

    先剥掉不该拿去核对的数字：电话、邮箱、URL、年份（1900-2100）。
    """
    text = re.sub(r"\b\d{3}[-\s]?\d{4}[-\s]?\d{4}\b", " ", text)      # 手机号
    text = re.sub(r"[\w.\-+]+@[\w.\-]+\.\w+", " ", text)               # 邮箱
    text = re.sub(r"https?://\S+", " ", text)                              # URL
    text = re.sub(r"(?<!\d)(19|20)\d{2}(?!\d)", " ", text)               # 年份
    out = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*万", text):
        try:
            out.add(str(int(float(m.group(1)) * 10000)))
        except ValueError:
            pass
    for m in re.finditer(r"(?<![\d.])(\d{2,})(?![\d.])", text):
        out.add(m.group(1))
    return out


def lint_entry(e):
    """返回 [(level, msg, code)]，level ∈ FAIL / WARN。"""
    body = (e.get("cover") or "") + " " + (e.get("subject") or "")
    repos = extract_repos(body)
    sources = e.get("sources") or []
    problems = []

    # 实物池：sources 声明 + 正文链接
    roots, cloud_notes = [], []
    for s in sources:
        kind = (s.get("kind") or "").lower()
        if kind == "repo":
            d = repo_dir((s.get("ref") or "").split("/")[-1])
            if d:
                roots.append((s.get("ref") or str(d), d))
            else:
                problems.append(("WARN", f"sources 声明的仓库 {s.get('ref')} 本机不存在", "SRC_MISSING"))
        elif kind == "private":
            p = Path(os.path.expanduser(s.get("path") or ""))
            if p.is_dir():
                roots.append((s.get("desc") or str(p), p))
            else:
                problems.append(("WARN", f"sources 声明的私有目录不存在：{s.get('path')}", "SRC_MISSING"))
        elif kind == "cloud":
            cloud_notes.append(s.get("desc") or "")
    for r in repos:
        d = repo_dir(r)
        if d and all(str(d) != str(x[1]) for x in roots):
            roots.append((f"Liooo0/{r}", d))

    # 1) 平台/框架名 → 必须有实物痕迹
    #    长名别名兜底：声称「Chroma」而实物里是「ChromaDB」（pip 包名/导入名）也算
    #    有痕迹——右边界正则只会整词匹配，chromadb 不算命中 Chroma，会误报
    #    TOKEN_NO_EVIDENCE。方向不可反：声称 ChromaDB 不能只靠 chroma 字样作数。
    for tok in PLATFORMS:
        if not token_rx(tok).search(body):
            continue
        alts = [p for p in PLATFORMS if p != tok and p.lower().startswith(tok.lower())]
        if any(grep_token(d, tok) or any(grep_token(d, a) for a in alts)
               for _, d in roots):
            continue
        if any(token_rx(tok).search(n) for n in cloud_notes):
            problems.append(("WARN", f"「{tok}」只有云端声明，需人工确认可演示", "CLOUD_ONLY"))
            continue
        if not roots and not cloud_notes:
            problems.append(("WARN", f"正文提到「{tok}」，条目没有任何 sources，无法核验", "NO_SOURCES"))
        else:
            names = "、".join([l for l, _ in roots] + cloud_notes) or "所声明实物"
            problems.append(("FAIL", f"正文声称用过「{tok}」，但 {names} 里查无此项", "TOKEN_NO_EVIDENCE"))

    # 2) 数字 → 必须能在 README 或事实清单里找到
    fact_text = FACTS.read_text(encoding="utf-8", errors="ignore") if FACTS.exists() else ""
    corpus = fact_text + "\n" + "\n".join(readme_text(d) for _, d in roots)
    corpus_nums = norm_numbers(corpus)
    for n in norm_numbers(body):
        if n not in corpus_nums:
            problems.append(("WARN", f"数字 {n} 在所附仓库 README 与事实清单里找不到出处", "NUM_UNSOURCED"))

    # 3) 禁用词
    for w in load_denylist():
        if w in body:
            problems.append(("FAIL", f"命中禁用词「{w}」（声称范围超出实物）", "DENYLIST"))

    # 4) 业务域 vs 实物主题
    body_domains = domain_groups(body)
    if body_domains and roots:
        repo_domains = set()
        for _, d in roots:
            repo_domains |= domain_groups(readme_text(d))
        if repo_domains and not (body_domains & repo_domains):
            problems.append(("WARN", f"正文讲「{'/'.join(sorted(body_domains))}」，"
                                     f"所附实物主题是「{'/'.join(sorted(repo_domains))}」，疑似贴错链接",
                             "DOMAIN_MISMATCH"))
    return problems


def collect(args):
    entries = []
    for f in sorted(QUEUE.glob("M*.json")):
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if args.ids and e.get("id") not in args.ids:
            continue
        if args.sent_today:
            if e.get("status") != "sent":
                continue
            if not str(e.get("sent_at") or "").startswith(date.today().isoformat()):
                continue
        elif not args.ids and e.get("status") not in ("draft", "approved", "hold"):
            continue
        entries.append(e)
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--sent-today", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    entries = collect(args)
    results, n_fail, n_warn = [], 0, 0
    for e in entries:
        probs = lint_entry(e)
        results.append({"id": e.get("id"), "company": e.get("company"),
                        "status": e.get("status"), "problems": probs,
                        "ok": not any(p[0] == "FAIL" for p in probs)})
        n_fail += sum(1 for p in probs if p[0] == "FAIL")
        n_warn += sum(1 for p in probs if p[0] == "WARN")

    if args.json:
        print(json.dumps({"results": results, "fail": n_fail, "warn": n_warn},
                         ensure_ascii=False, indent=1))
    else:
        for r in results:
            mark = "✅" if not r["problems"] else ("❌" if not r["ok"] else "⚠️")
            print(f"{mark} {r['id']} [{r['status']}] {str(r['company'])[:20]}")
            for lvl, msg, code in r["problems"]:
                print(f"    {lvl}[{code}]: {msg}")
        print(f"\n共查 {len(results)} 条：FAIL {n_fail} / WARN {n_warn}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

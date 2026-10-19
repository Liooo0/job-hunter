#!/usr/bin/env python3
"""official_mail_apply.py — 官网/外企/管培 邮件直投管线（三阶段）· V1（2026-10-17）

用法：
    PYTHONPATH="" /usr/bin/python3 official_mail_apply.py --draft
    PYTHONPATH="" /usr/bin/python3 official_mail_apply.py --list
    PYTHONPATH="" /usr/bin/python3 official_mail_apply.py --enrich   # 只读探针补邮箱（A轨）+ 纯网申分流（B轨）
    PYTHONPATH="" /usr/bin/python3 official_mail_apply.py --approve all    # 或 --approve <id>
    PYTHONPATH="" /usr/bin/python3 official_mail_apply.py --send           # 只发已批准且带邮箱的

阶段纪律（与 Gemini 补丁规格一致）：
    --draft    解析《外企可投清单.md》+《投递作战表.md》→ 按岗位方向路由 4 份简历变体
               → 生成求职信 → 强制过 provenance 双闸（数字闸 + 技术栈闸）
               → 写入 data/mail_queue/<id>.json（status=draft；未过闸 = gate_failed，不可发）
    --approve  人工把 draft 置 approved（从不自动批准；探针发现的邮箱在批准时转正为 hr_email）
    --enrich   只读补邮箱（A 轨：复用 scripts/official_career_probe.py 的 curl 抓取，提取
               页面公开邮箱 → discovered_email/email_source_url，等 --approve 转正）+ 纯网申
               分流（B 轨：无公开邮箱的条目标 channel="portal_only" 并汇总到
               data/mail_queue/PORTAL_APPLY_TODO.md）。限速只读、从不发送、从不编造邮箱。
    --send     仅发送 approved 且 hr_email 非空的条目：smtp.qq.com:465 SSL（From 纯 ASCII 防 550）
               → 追加到 IMAP "Sent Messages" 留档
               → record_application(platform="direct_mail", status="APPLIED", verified=1) 落库
               发送前 Format Guard 二次校验（附件存在 + 正文无残留占位符 + 邮箱格式）。

隐私与纪律：
    · 求职者姓名/电话/邮箱不硬编码在本文件 —— 读本机 data/mailer_identity.json
      （data/ 已 gitignore，永不入库）；
    · 从不编造邮箱：清单里没有邮箱的条目排队为 needs_email=true，等补齐后再发；
    · 队列/身份文件只在本机。
"""
import argparse
import hashlib
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path

SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

QUEUE_DIR = SKILL_DIR / "data" / "mail_queue"
IDENTITY_FILE = SKILL_DIR / "data" / "mailer_identity.json"
QQ_ENV_FILE = Path.home() / ".gamebanana-monitor.env"

# ── 简历变体（文件名均不含个人姓名；EN 版用 glob 匹配，避免在代码里写真实姓名）──
RESUME_SPECS = [
    ("ai", "投递用_AI应用主简历.pdf", "AI 应用主简历"),
    ("impl", "投递用_实施解决方案简历.pdf", "实施/解决方案"),
    ("supply", "投递用_采购供应链简历.pdf", "采购供应链"),
    ("en", "*Resume-EN-G*.pdf", "英文 G 版"),
]
VARIANT_LABEL = {k: label for k, _p, label in RESUME_SPECS}
DEFAULT_VARIANT = "ai"


def _doc_bases():
    """源文档目录（iCloud 文稿目录名含个人姓名 → 用 glob 规避硬编码）。"""
    bases = []
    for d in (Path.home() / "Documents").glob("文稿*"):
        bases.append(d / "求职资料")
        bases.append(d)
    bases.append(Path.home() / "Documents" / "求职资料")
    return bases


def _resume_bases():
    bases = []
    for d in (Path.home() / "Documents").glob("文稿*"):
        bases.append(d)
        bases.append(d / "求职资料" / "简历")
    bases.append(Path.home() / "Documents" / "求职资料" / "简历")
    bases.append(Path.home() / "Documents" / "求职资料")
    return bases


def find_resume(variant: str):
    """按变体找本机简历文件；找不到返回 None（条目仍排队，只是不可发）。"""
    for key, pattern, _label in RESUME_SPECS:
        if key != variant:
            continue
        for base in _resume_bases():
            if "*" in pattern:
                for p in sorted(base.glob(pattern)):
                    return p
            else:
                p = base / pattern
                if p.exists():
                    return p
    return None


def route_resume(*texts) -> str:
    """按岗位方向路由简历变体：采购/供应链 > 实施/解决方案 > 英文岗 > 默认 AI 主简历。"""
    blob = " ".join(t or "" for t in texts)
    if re.search(r"采购|供应链|sourcing|procurement|buyer", blob, re.I):
        return "supply"
    if re.search(r"实施|解决方案|技术支持|售前|RPA|数字化|交付|运维|FDE", blob, re.I):
        return "impl"
    if re.search(r"英文|英语|English|Global|海外|\bEN\b", blob, re.I):
        return "en"
    return DEFAULT_VARIANT


def parse_waiqi_md(text: str) -> list:
    """解析《外企可投清单.md》：A/B 档表格行 → 候选列表。"""
    out, bucket = [], ""
    for line in text.splitlines():
        m_sec = re.match(r"^##\s*([AB])\s*档", line)
        if m_sec:
            bucket = m_sec.group(1)
            continue
        if not bucket or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or cells[0] in ("#", ""):
            continue
        if set(cells[1]) <= set("-: "):
            continue
        m_link = re.search(r"\((https?://[^)]+)\)", cells[6])
        out.append({
            "source": "外企可投清单", "bucket": f"{bucket}档",
            "title": cells[1], "company": cells[2],
            "location": cells[3], "salary": cells[4], "judge": cells[5],
            "link": m_link.group(1) if m_link else "",
            "hr_email": "",
        })
    return out


def parse_zuozhan_md(text: str) -> list:
    """解析《投递作战表.md》「作战清单」段：编号条目 + 子行 → 候选列表。"""
    blocks, cur, in_list = [], None, False
    for line in text.splitlines():
        if re.match(r"^##\s*作战清单", line):
            in_list = True
            continue
        if in_list and line.startswith("## "):
            break
        if not in_list:
            continue
        m = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if m:
            if cur:
                blocks.append(cur)
            cur = [m.group(1).strip()]
        elif cur is not None and line.strip():
            cur.append(line.strip())
    if cur:
        blocks.append(cur)
    out = []
    for block in blocks:
        body = "\n".join(block)
        first = block[0]
        title = re.split(r"——|—", first)[0].strip()
        urls = re.findall(r"https?://[^\s)）]+", body)
        emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", body)
        out.append({
            "source": "投递作战表", "bucket": "管培",
            "title": title,
            "company": (title.split()[0] if title.split() else title),
            "location": "", "salary": "", "judge": "",
            "link": urls[0] if urls else "",
            "hr_email": emails[0] if emails else "",
        })
    return out


def load_identity():
    if not IDENTITY_FILE.exists():
        print(f"❌ 缺少本机身份文件 {IDENTITY_FILE}（含姓名/联系方式，永不入库）")
        print('   需要内容：{"name": "…", "email": "…", "phone": "…"}')
        return None
    return json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))


def make_cover(identity: dict, entry: dict) -> str:
    """确定性求职信（不写电话/邮箱/未证数字；过 provenance 双闸后才可发）。"""
    name = identity["name"]
    title = entry["title"]
    return (
        f"你好，我是{name}，2025 届本科（工商管理），现居深圳，专注 AI 应用交付。\n"
        f"在招的「{title}」与我的方向对口：我独立交付过 RAG 知识库客服系统与多平台"
        f"数据自动化管线，熟悉 Python、LLM API 集成与浏览器自动化。\n"
        f"附件是我的简历，期待与您进一步沟通。\n"
        f"{name}"
    )


_CORPUS: dict = {"c": None}


def _corpus():
    if _CORPUS["c"] is None:
        corpus = {}
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_crd_mail", str(SKILL_DIR / "scripts" / "check_reply_drafts.py"))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                corpus = mod.load_corpus() or {}
        except Exception:
            pass
        _CORPUS["c"] = corpus
    return _CORPUS["c"]


def gate_text(text: str):
    """provenance 双闸（数字闸 + 技术栈闸）。返回 (ok, issues)。"""
    try:
        import provenance as pv
        corpus = _corpus()
        if not corpus:
            return False, ["no_corpus"]
        num = pv.gate_outgoing_text(text, corpus)
        tech = pv.check_tech_claims(text, corpus)
        issues = list(num.get("blocked") or []) + list(tech.get("unsupported") or [])
        return (not issues), issues
    except Exception as e:
        return False, [f"gate_err:{e}"]


def _entry_id(entry: dict) -> str:
    raw = entry.get("company", "") + entry.get("title", "") + entry.get("link", "")
    return "M" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def draft_all() -> int:
    ident = load_identity()
    if not ident:
        return 1
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    # 同名文档可能有多份拷贝（iCloud 文稿目录 / 本地求职资料）→ 每个名字只取最新 mtime 的一份
    picked = {}
    for base in _doc_bases():
        for kind, rel in (("waiqi", Path("外企可投清单.md")),
                          ("zuozhan", Path("管培投递-2026-10") / "投递作战表.md")):
            p = base / rel
            if not p.exists():
                continue
            name = rel.name
            mt = p.stat().st_mtime
            cur = picked.get(name)
            if cur is None or mt > cur[0]:
                picked[name] = (mt, kind, p)
    for _mt, kind, path in picked.values():
        text = path.read_text(encoding="utf-8", errors="ignore")
        entries += parse_waiqi_md(text) if kind == "waiqi" else parse_zuozhan_md(text)
    new = gate_fail = dup = 0
    for e in entries:
        eid = _entry_id(e)
        qf = QUEUE_DIR / f"{eid}.json"
        if qf.exists():
            dup += 1
            continue
        variant = route_resume(e.get("title", ""), e.get("company", ""))
        rp = find_resume(variant)
        cover = make_cover(ident, e)
        ok, issues = gate_text(cover)
        item = {
            "id": eid, "source": e["source"], "bucket": e.get("bucket", ""),
            "company": e["company"], "title": e["title"],
            "location": e.get("location", ""), "salary": e.get("salary", ""),
            "judge": e.get("judge", ""), "link": e.get("link", ""),
            "hr_email": e.get("hr_email", ""),
            "needs_email": not bool(e.get("hr_email")),
            "resume_variant": variant, "resume_label": VARIANT_LABEL.get(variant, ""),
            "resume_path": str(rp) if rp else None,
            "subject": f"应聘{e['title']} — {ident['name']}",
            "cover": cover,
            "gate": {"passed": ok, "issues": issues},
            "status": "draft" if ok else "gate_failed",
            "drafted_at": datetime.now().isoformat(timespec="seconds"),
            "approved_at": None, "sent_at": None, "message_id": None,
        }
        qf.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        if ok:
            new += 1
        else:
            gate_fail += 1
        flag = "✅" if ok else "⛔"
        extra = "" if ok else f" | issues={issues}"
        warn = "" if rp else " | ⚠️无简历文件"
        print(f"  [{flag}] {eid} {e['company'][:18]} | {e['title'][:30]} | {variant}{warn}{extra}")
    print(f"\n📦 draft 完成：新增 {new} / 闸门未过 {gate_fail} / 已存在跳过 {dup}")
    print(f"   队列目录：{QUEUE_DIR}")
    return 0


def approve(target: str) -> int:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for qf in sorted(QUEUE_DIR.glob("*.json")):
        it = json.loads(qf.read_text(encoding="utf-8"))
        if target != "all" and it.get("id") != target:
            continue
        if it.get("status") != "draft":
            print(f"  ⏭️ {it.get('id')} 状态 {it.get('status')}，跳过")
            continue
        # V5.7：批准语义 = 人工确认该收件地址可用（探针发现的邮箱此时转正）
        _promote_email(it)
        it["status"] = "approved"
        it["approved_at"] = datetime.now().isoformat(timespec="seconds")
        qf.write_text(json.dumps(it, ensure_ascii=False, indent=2), encoding="utf-8")
        n += 1
        print(f"  ✅ [approved] {it['id']} {it['company'][:18]} | {it['title'][:30]}")
    print(f"共批准 {n} 条")
    return 0


def list_queue() -> int:
    rows = sorted(QUEUE_DIR.glob("*.json")) if QUEUE_DIR.exists() else []
    for qf in rows:
        it = json.loads(qf.read_text(encoding="utf-8"))
        if it.get("hr_email"):
            mail = "mail:" + it["hr_email"]
        elif it.get("discovered_email"):
            mail = "found:" + it["discovered_email"]
        elif it.get("channel") == "portal_only":
            mail = "portal_only"
        else:
            mail = "needs_email"
        print(f"  {it['id']} | {it['status']:12s} | {it['company'][:18]:20s} | "
              f"{it['title'][:30]:32s} | {it['resume_variant']:6s} | {mail}")
    print(f"共 {len(rows)} 条")
    return 0


def format_guard(entry: dict):
    """发送前硬校验：附件存在 + 正文无残留占位符 + 邮箱格式。返回 (ok, why)。"""
    cover = entry.get("cover") or ""
    if "{" in cover or "}" in cover:
        return False, "format_guard:正文有残留占位符"
    to_addr = entry.get("hr_email") or ""
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to_addr):
        return False, "format_guard:收件邮箱缺失/格式异常"
    rp = entry.get("resume_path")
    if not rp or not Path(rp).exists():
        return False, "format_guard:简历附件不存在"
    return True, "ok"


def _env_file(path: Path) -> dict:
    vals = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def send_entry(entry: dict, identity: dict):
    """发送单条（SMTP + Sent 留档）。返回 (ok, message_id_or_err)。"""
    ok, why = format_guard(entry)
    if not ok:
        return False, why
    env = _env_file(QQ_ENV_FILE)
    pwd = env.get("QQ_EMAIL_PASSWORD")
    from_addr = identity.get("email") or env.get("QQ_EMAIL_USER") or ""
    if not re.fullmatch(r"[\x21-\x7e]+", from_addr or ""):
        return False, "format_guard:发件人必须是纯 ASCII"
    if not pwd:
        return False, "缺少 QQ_EMAIL_PASSWORD（~/.gamebanana-monitor.env）"
    to_addr = entry["hr_email"]

    msg = MIMEMultipart("mixed")
    msg["From"] = from_addr          # 纯 ASCII，防 550 拒信
    msg["To"] = to_addr
    msg["Subject"] = Header(entry.get("subject") or f"应聘{entry.get('title', '')}", "utf-8").encode()
    msg["Date"] = formatdate(localtime=True)
    msg_id = make_msgid()
    msg["Message-ID"] = msg_id
    msg.attach(MIMEText(entry["cover"], "plain", "utf-8"))
    rp = Path(entry["resume_path"])
    att = MIMEApplication(rp.read_bytes(), Name=rp.name)
    att["Content-Disposition"] = f'attachment; filename="{Header(rp.name, "utf-8").encode()}"'
    msg.attach(att)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=90, context=ctx) as s:
        s.login(from_addr, pwd)
        s.sendmail(from_addr, [to_addr], msg.as_string())
    try:
        import imaplib
        m = imaplib.IMAP4_SSL("imap.qq.com", 993, timeout=60)
        m.login(from_addr, pwd)
        m.append("Sent Messages", '\\Seen', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        m.logout()
    except Exception as e:
        print(f"    ⚠️ 已发出但 Sent 留档失败（不影响送达）: {e}")
    return True, msg_id


def record(entry: dict, msg_id: str) -> bool:
    try:
        from store import record_application
        record_application(
            platform="direct_mail", city="", company=entry.get("company", ""),
            title=entry.get("title", ""), salary=entry.get("salary", ""),
            keyword="直投", score=0, resume_version=entry.get("resume_variant", ""),
            decision="applied", status="APPLIED",
            reason=f"邮件直投:{entry.get('hr_email', '')}"[:80], verified=1,
            event_type="apply", event_error=None,
            extra_payload={
                "hr_email": entry.get("hr_email"),
                "company_domain": (entry.get("hr_email") or "").split("@")[-1],
                "resume_variant": entry.get("resume_variant"),
                "followup_stage": 0, "message_id": msg_id,
                "source": entry.get("source"), "link": entry.get("link"),
            },
            gates=None, greeting_template_id=None,
        )
        return True
    except Exception as e:
        print(f"    ⚠️ 落库失败: {e}")
        return False


# ── V5.7 收口：--enrich 只读探针（A 轨：找公开邮箱）+ 纯网申分流（B 轨）────────
PORTAL_TODO = QUEUE_DIR / "PORTAL_APPLY_TODO.md"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMAIL_DENY = ("sentry", "wixpress", "example.", "noreply", "no-reply",
               "your@", "name@", "email@", "user@", "test@")

_PROBE: dict = {"m": None}


def _probe():
    """复用 scripts/official_career_probe.py 的 curl 抓取（只读 GET）。"""
    if _PROBE["m"] is None:
        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location(
                "_ocp", str(SKILL_DIR / "scripts" / "official_career_probe.py"))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _PROBE["m"] = mod
            else:
                _PROBE["m"] = False
        except Exception:
            _PROBE["m"] = False
    return _PROBE["m"] or None


def extract_emails(raw: str) -> list:
    """从页面原文提取候选投递邮箱（去噪 + 去重，最多 5 个）。"""
    if not raw:
        return []
    out = []
    for m in _EMAIL_RE.finditer(raw):
        e = m.group(0).strip(".,;:<>()[]'\"").lower()
        if any(d in e for d in _EMAIL_DENY):
            continue
        if e not in out:
            out.append(e)
        if len(out) >= 5:
            break
    return out


# ── V5.8（2026-10-18）：9224 端口 Chrome 只读渲染兜底 ─────────────────────────
_CHROME_PORT = int(os.environ.get("JH_ENRICH_CDP_PORT", "9224"))
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")


def chrome_fetch(url: str, port: int = None, wait: float = 3.0):
    """9224 端口 Chrome 只读渲染兜底（curl 被 WAF 拦 / SPA 空壳时用）。

    只做「开新页 → 读 HTML → 关页」，不点击、不输入、不写任何东西。
    返回 (ok, html, why)。
    """
    port = int(port or _CHROME_PORT)
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except Exception as e:
        return False, "", f"DrissionPage 不可用: {type(e).__name__}"
    tab = None
    try:
        page = ChromiumPage(ChromiumOptions().set_local_port(port))
        tab = page.new_tab(url)
        time.sleep(wait)
        html = getattr(tab, "html", "") or ""
        return True, html[:500_000], ""
    except Exception as e:
        return False, "", f"Chrome({port}) 渲染失败: {type(e).__name__}"
    finally:
        try:
            if tab is not None:
                tab.close()
        except Exception:
            pass


def _looks_like_spa_shell(body: str) -> bool:
    """页面像 JS 空壳：可见文本极少、脚本块却不少（curl 拿不到真实内容）。"""
    if not body:
        return True
    if len(body) < 400:
        return True
    visible = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", body)
    visible = re.sub(r"\s+", " ", visible).strip()
    return len(visible) < 60 and body.lower().count("<script") >= 2


def probe_with_fallback(link: str, probe_mod=None):
    """抓一条链接：curl 优先；403/000/空壳时切 Chrome 只读渲染兜底。

    返回 (emails, note, channel)：channel = "curl" / "chrome" / ""（都未成）。
    """
    if not link:
        return [], "条目无链接", ""
    probe_mod = probe_mod if probe_mod is not None else _probe()
    code, body, need_browser = "", "", False
    note = ""
    if probe_mod:
        try:
            code, body = probe_mod.fetch(link)
        except Exception:
            code, body = "", ""
        if str(code).startswith("2") and body:
            emails = extract_emails(body)
            if emails:
                return emails, f"来自 {link}", "curl"
            need_browser = _looks_like_spa_shell(body)
            note = f"页面可读（HTTP {code}）但未见公开邮箱" + ("（空壳页）" if need_browser else "")
        else:
            need_browser = True
            note = f"只读探针失败（HTTP {code}）"
    else:
        need_browser = True
        note = "探针模块不可用"
    if need_browser:
        ok, html, why = chrome_fetch(link)
        if ok and html:
            emails = extract_emails(html)
            if emails:
                return emails, f"来自 {link}（浏览器渲染兜底）", "chrome"
            base = note or f"HTTP {code}"
            note = f"{base}；浏览器渲染兜底未检出邮箱"
        elif note:
            note = f"{note}；浏览器兜底失败：{why or '未成功'}"
        else:
            note = f"浏览器兜底失败：{why or '未成功'}"
    return [], note, ""


def _promote_email(it: dict) -> dict:
    """批准语义 = 人工确认该收件地址可用；探针发现的邮箱在批准时转正。"""
    if not it.get("hr_email") and it.get("discovered_email"):
        it["hr_email"] = it["discovered_email"]
        it["needs_email"] = False
        it["email_confirmed_by"] = "approve"
    return it


def enrich_all() -> int:
    """--enrich：只读补邮箱（A 轨）+ 纯网申分流汇总（B 轨）。从不发送、从不编造。"""
    if not QUEUE_DIR.exists():
        print("队列为空，先跑 --draft")
        return 1
    probe = _probe()
    if not probe:
        print("⚠️ 探针模块加载失败（scripts/official_career_probe.py）")
    found = portal = failed = skipped = 0
    portal_items = []
    for qf in sorted(QUEUE_DIR.glob("*.json")):
        it = json.loads(qf.read_text(encoding="utf-8"))
        if it.get("status") != "draft":
            continue
        if it.get("hr_email") or it.get("discovered_email") or it.get("channel"):
            skipped += 1
            continue
        link = it.get("link") or ""
        # V5.8：curl 优先 + 9224 浏览器只读渲染兜底（403/000/SPA 空壳自动切换）
        emails, note, _channel = probe_with_fallback(link, probe_mod=probe)
        if emails:
            it["discovered_email"] = emails[0]
            it["email_source_url"] = link
            it["enrich_note"] = note or f"来自 {link}"
            if _channel == "chrome":
                it["email_probe_channel"] = "chrome_render"
            found += 1
            flag = "📧"
        elif note.startswith("只读探针失败"):
            it["enrich_note"] = note
            failed += 1
            flag = "⏳"
        else:
            it["channel"] = "portal_only"
            it["enrich_note"] = note
            portal_items.append(it)
            portal += 1
            flag = "📋"
        it["enriched_at"] = datetime.now().isoformat(timespec="seconds")
        qf.write_text(json.dumps(it, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(random.uniform(1.2, 2.6))
        label = emails[0] if emails else it["enrich_note"][:36]
        print(f"  {flag} {it['id']} {it['company'][:16]} | {label}")
    if portal_items:
        lines = ["# 纯网申通道待办（portal_only）", "",
                 f"生成于 {datetime.now().isoformat(timespec='seconds')} · 共 {len(portal_items)} 条 · "
                 "由 official_mail_apply --enrich 产出（无公开邮箱、走官网表单的条目）", ""]
        for i, it in enumerate(portal_items, 1):
            lines.append(f"## {i}. {it['company']} — {it['title']}")
            lines.append(f"- 入口: {it.get('link') or '（无链接，需人工补入口）'}")
            if it.get("resume_path"):
                lines.append(f"- 简历: {Path(it['resume_path']).name}（{it['resume_path']}）")
            lines.append(f"- 备注: {it.get('enrich_note', '')}")
            lines.append("- 求职信（可直接粘贴）:")
            for ln in (it.get("cover") or "").splitlines():
                lines.append(f"  > {ln}")
            lines.append("")
        PORTAL_TODO.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n📋 B 轨汇总已写入：{PORTAL_TODO}（{len(portal_items)} 条）")
    print(f"\n📧 enrich 完成：找到邮箱 {found} / 确认纯网申 {portal} / 探针失败 {failed} / 已处理跳过 {skipped}")
    return 0


# ── V5.8（2026-10-18）：IMAP 退信回读（PostMaster → status=bounced）──────────
BOUNCE_STATE = SKILL_DIR / "data" / "mail_bounce_state.json"

_BOUNCE_HINT = ("退信", "returned mail", "mail delivery", "delivery status",
                "delivery failure", "undeliver", "投递失败", "无法投递", "failure notice")
# 主题级特征（收口误报：正文里提到"退信"字样的普通往来邮件不得误判为退信）
_BOUNCE_SUBJ = ("退信", "undelivered", "undeliverable", "returned mail", "delivery status",
                "delivery fail", "failure notice", "无法投递", "投递失败", "returned to sender")


def _dec_hdr(s) -> str:
    """头字段安全解码：str / bytes / email.header.Header / None 一律返回 str。

    V5.8-fix：QQ 系统退信的主题在部分邮件里以 Header 对象返回（非 8bit 清理过的
    原始头），旧的局部解码会原样返回该对象，随后字符串拼接抛 TypeError。
    """
    from email.header import decode_header
    if s is None:
        return ""
    if not isinstance(s, (str, bytes)):
        try:
            s = str(s)
        except Exception:
            return ""
    try:
        out = []
        for txt, enc in decode_header(s):
            if isinstance(txt, bytes):
                try:
                    out.append(txt.decode(enc or "utf-8", "replace"))
                except LookupError:  # 如 QQ 退信头的 charset=unknown-8bit
                    out.append(txt.decode("utf-8", "replace"))
            else:
                out.append(txt)
        return "".join(out)
    except Exception:
        return s.decode("utf-8", "replace") if isinstance(s, bytes) else s


_DIAG_RE = re.compile(
    r"Diagnostic-Code:\s*(?:smtp;\s*)?([^\n\r]{5,200})", re.I)
_STATUS_RE = re.compile(r"\bStatus:\s*(\d+\.\d+\.\d+)", re.I)
_RCPT_RE = re.compile(r"Final-Recipient:\s*rfc822;\s*([^\s;]+)", re.I)
_MID_RE = re.compile(r"(?:Original-)?Message-ID:\s*(<[^>]+>)", re.I)


def parse_bounce(raw: bytes):
    """解析一封退信 → dict（尽力而为，提取不到即留空）。

    兼容标准 DSN（multipart/report + delivery-status）与 QQ 中文退信。
    返回 None 表示这不是退信。
    """
    import email as _email

    try:
        msg = _email.message_from_bytes(raw)
    except Exception:
        return None
    subj = _dec_hdr(msg.get("Subject", ""))
    chunks = []
    for part in msg.walk():
        try:
            pl = part.get_payload()
            # message/delivery-status 与内嵌 message/rfc822：载荷是子 Message 列表
            if isinstance(pl, list):
                for sub in pl:
                    try:
                        chunks.append(str(sub))
                    except Exception:
                        pass
                continue
            payload = part.get_payload(decode=True)
            if payload:
                chunks.append(payload.decode(part.get_content_charset() or "utf-8",
                                             "replace"))
            elif isinstance(pl, str):
                chunks.append(pl)
        except Exception:
            continue
    text = "\n".join(chunks)
    frm_l = _dec_hdr(msg.get("From", "")).lower()
    types = set()
    for _p in msg.walk():
        try:
            types.add(_p.get_content_type())
        except Exception:
            continue
    struct_ok = "message/delivery-status" in types
    subj_ok = any(k in subj.lower() for k in _BOUNCE_SUBJ)
    sender_ok = any(k in frm_l for k in ("mailer-daemon", "postmaster"))
    if not (struct_ok or subj_ok
            or (sender_ok and any(h in (subj.lower() + text.lower()) for h in _BOUNCE_HINT))):
        return None
    m = _MID_RE.search(text) or _MID_RE.search(subj)
    rcpt = _RCPT_RE.search(text)
    diag = _DIAG_RE.search(text)
    reason = (diag.group(1).strip() if diag else "")
    if not reason:
        st = _STATUS_RE.search(text)
        reason = st.group(1) if st else ""
    if not reason:
        for ln in text.splitlines():
            l = ln.strip()
            if any(k in l for k in ("5.7.0", "552", "550", "554", "451", "421",
                                    "rejected", "spam", "denied", "blocked")):
                reason = l[:200]
                break
    return {
        "bounce_subject": subj[:160],
        "orig_message_id": (m.group(1).strip() if m else ""),
        "recipient": (rcpt.group(1).strip() if rcpt else ""),
        "reason": reason[:220],
    }


def apply_bounce(parsed: dict, queue_dir: Path = None) -> str:
    """把退信落到队列条目：status=bounced + 原因摘要。返回命中条目 id（未命中 ""）。"""
    if not parsed:
        return ""
    qdir = Path(queue_dir) if queue_dir else QUEUE_DIR
    mid = parsed.get("orig_message_id") or ""
    subj = parsed.get("bounce_subject") or ""
    reason = parsed.get("reason") or "未提取到原因"
    for qf in sorted(qdir.glob("M*.json")):
        try:
            it = json.loads(qf.read_text(encoding="utf-8"))
        except Exception:
            continue
        hit = False
        if mid and it.get("message_id") == mid:
            hit = True
        elif it.get("id") and it.get("id") in (subj + reason):
            hit = True
        elif subj and it.get("subject") and str(it["subject"])[:24] in subj:
            hit = True
        if hit:
            it["status"] = "bounced"
            it["bounced_at"] = datetime.now().isoformat(timespec="seconds")
            it["bounce_reason"] = reason
            if parsed.get("recipient"):
                it["bounce_recipient"] = parsed["recipient"]
            qf.write_text(json.dumps(it, ensure_ascii=False, indent=2), encoding="utf-8")
            return it["id"]
    return ""


def check_bounces() -> int:
    """IMAP 回读退信：命中即更新队列为 status=bounced。返回命中更新条数。"""
    import imaplib
    env = {}
    if QQ_ENV_FILE.exists():
        for line in QQ_ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    user = env.get("QQ_EMAIL_USER") or ""
    pwd = env.get("QQ_EMAIL_PASSWORD") or ""
    if not user or not pwd:
        print("❌ 未找到 QQ_EMAIL_USER / QQ_EMAIL_PASSWORD（~/.gamebanana-monitor.env）")
        return 0
    state = {"seen": []}
    try:
        if BOUNCE_STATE.exists():
            state.update(json.loads(BOUNCE_STATE.read_text(encoding="utf-8")))
    except Exception:
        pass
    seen = set(state.get("seen", []))
    handled = 0
    try:
        M = imaplib.IMAP4_SSL("imap.qq.com", 993, timeout=30)
        M.login(user, pwd)
        M.select("INBOX", readonly=True)
        typ, data = M.search(None, "ALL")
        ids = data[0].split()[-400:]
        for i in ids[::-1]:
            key = i.decode()
            if key in seen:
                continue
            typ, md = M.fetch(i, "(RFC822)")
            if not md or not md[0]:
                continue
            parsed = parse_bounce(md[0][1])
            seen.add(key)          # 非退信也登记，避免每轮重扫
            if not parsed:
                continue
            hit_id = apply_bounce(parsed)
            if hit_id:
                handled += 1
                print(f"  ↩️ 退信命中 {hit_id}：{parsed.get('reason', '')[:80]}")
            else:
                print(f"  ↩️ 退信未命中队列（{parsed.get('bounce_subject', '')[:60]}）")
        M.logout()
    except Exception as e:
        print(f"❌ 退信回读失败: {type(e).__name__}: {str(e)[:120]}")
        return handled
    try:
        state["seen"] = sorted(seen)[-800:]
        state["last_run"] = datetime.now().isoformat(timespec="seconds")
        BOUNCE_STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    print(f"📭 退信回读完成：命中并更新 {handled} 条")
    return handled


def send_all() -> int:
    ident = load_identity()
    if not ident:
        return 1
    sent = skip = fail = 0
    for qf in sorted(QUEUE_DIR.glob("*.json")):
        it = json.loads(qf.read_text(encoding="utf-8"))
        if it.get("status") != "approved":
            continue
        if not it.get("hr_email"):
            print(f"  ⏭️ {it['id']} 无邮箱（needs_email=true），等补齐后重发")
            skip += 1
            continue
        rp = Path(it["resume_path"]).name if it.get("resume_path") else "N/A"
        print(f"  📤 {it['id']} → {it['hr_email']} | {it['company'][:18]} | 附件 {rp}")
        ok, info = send_entry(it, ident)
        if ok:
            sent += 1
            it.update({"status": "sent", "sent_at": datetime.now().isoformat(timespec="seconds"),
                       "message_id": info})
            record(it, info)
            print(f"    ✅ 已发送（SMTP 受理 + Sent 留档）message_id={info}")
            time.sleep(random.uniform(15, 40))
        else:
            fail += 1
            it["last_error"] = info
            print(f"    ❌ {info}")
        qf.write_text(json.dumps(it, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📮 send 完成：成功 {sent} / 无邮箱跳过 {skip} / 失败 {fail}")
    return 0


def main() -> int:
    from shared import install_stdout_tee   # V5.8：所有运行模式 stdout 自动留档
    install_stdout_tee("direct_mail")
    ap = argparse.ArgumentParser(description="官网/外企/管培 邮件直投管线（三阶段）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--draft", action="store_true", help="解析清单 → 路由简历 → 生成求职信 → 过双闸 → 写队列")
    g.add_argument("--approve", metavar="ID|all", help="批准队列条目（人工放行）")
    g.add_argument("--send", action="store_true", help="发送已批准且带邮箱的条目")
    g.add_argument("--list", action="store_true", help="查看队列状态")
    g.add_argument("--enrich", action="store_true", help="只读探针补邮箱（A轨）+ 纯网申分流汇总（B轨）")
    g.add_argument("--bounces", action="store_true",
                   help="IMAP 回读退信：命中即把队列条目更新为 status=bounced")
    args = ap.parse_args()
    if args.draft:
        return draft_all()
    if args.enrich:
        return enrich_all()
    if args.bounces:
        check_bounces()
        return 0
    if args.approve is not None:
        return approve(args.approve)
    if args.send:
        return send_all()
    if args.list:
        return list_queue()
    return 0


if __name__ == "__main__":
    sys.exit(main())

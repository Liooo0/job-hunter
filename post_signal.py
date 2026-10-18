#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投后信号接线（V5.8 第一批，2026-10-18）

链路定位：HR 消息扫描（hr_auto_reply）与招聘邮件分拣（scripts/scan_job_mail）
一旦识别到「约面 / 索要简历」信号，就在本地生成该岗位的
【面试准备 + 存活核查】简报 —— 只读（抓页面）+ 只写 data/reports/，
不触碰投递/回复主流程，不发送任何内容。

简报内容：
  · 存活核查：岗位链接可读时用 liveness.classify 判定（过期/挑战页/不确定分明）；
    取不到页面时如实写「未核」，绝不编造；
  · 面试准备：interview_prep.build_prep（JD → 分受众问题 + 故事库映射 + 缺口），
    故事库 = datasets/public/story-bank.md；
  · 同一（公司+岗位+信号类型）只生成一次（状态在 data/signal_brief_state.json）。
"""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent
REPORT_DIR = BASE / "data" / "reports"
STATE_FILE = BASE / "data" / "signal_brief_state.json"
STORY_BANK = BASE / "datasets" / "public" / "story-bank.md"
CV_FILE = BASE / "data" / "my_profile.txt"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")


def classify_signal(msg: str) -> str:
    """「约面 / 要简历」信号（转发到 schedule_inquiry 的前置守卫链，单源）。"""
    try:
        import schedule_inquiry as _SI
        return _SI.classify_signal(msg)
    except Exception:
        return ""


def fetch_page_text(url: str, timeout: int = 15):
    """只读抓页面文本（curl 层）。返回 (code, text)。"""
    if not url:
        return "", ""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout),
             "-H", f"User-Agent: {_UA}", "-H", "Accept-Language: zh-CN,zh;q=0.9",
             "-w", "\n@@HTTP:%{http_code}", url],
            capture_output=True, text=True, errors="ignore")
        out = r.stdout or ""
        code = ""
        m = re.search(r"@@HTTP:(\d+)$", out)
        if m:
            code = m.group(1)
            out = out[:m.start()]
        return code, out
    except Exception:
        return "", ""


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "", (s or "").strip())
    return (s or "unknown")[:24]


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {"generated": {}}


def _save_state(d: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def on_signal(*, company: str, role: str = "", msg: str = "", jd: str = "",
              url: str = "", source: str = "", force: bool = False) -> Optional[Path]:
    """信号命中入口。返回生成的简报路径；无信号 / 已生成过返回 None 或既有路径。"""
    kind = classify_signal(msg)
    if not kind:
        return None
    label = {"interview": "约面", "resume": "要简历"}[kind]
    key = f"{(company or '').strip()}|{(role or '').strip()}|{kind}"
    state = _load_state()
    prev = (state.get("generated") or {}).get(key)
    if prev and not force and Path(prev.get("path", "")).exists():
        return Path(prev["path"])

    # ── 存活核查（如实：取不到就写「未核」）──
    live_state, live_detail = "未核", "无岗位链接或页面未取到"
    if url:
        code, text = fetch_page_text(url)
        if text:
            try:
                import liveness
                st, why = liveness.classify(text)
                live_state, live_detail = liveness.describe(st), f"HTTP {code}｜{why}"
            except Exception as e:
                live_detail = f"HTTP {code}｜判定失败: {type(e).__name__}"
        else:
            live_detail = f"链接不可达（HTTP {code or '000'}）"

    # ── 面试准备（确定性映射，不编造经历）──
    try:
        import provenance
        import interview_prep
        stories = provenance.load_story_bank(STORY_BANK) if STORY_BANK.exists() else []
        cv_text = (CV_FILE.read_text(encoding="utf-8", errors="ignore")
                   if CV_FILE.exists() else "")
        rep = interview_prep.build_prep(company or role or "该岗位", role or "", jd or "",
                                        stories, cv_text=cv_text)
        prep_text = rep.render()
    except Exception as e:
        prep_text = f"（面试准备生成失败：{type(e).__name__}: {str(e)[:100]}）"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = REPORT_DIR / f"brief_{_slug(company or role)}_{kind}_{ts}.md"
    fp.write_text(
        f"【投后简报】{company or '(未知公司)'} · {role or '(未记录岗位)'}\n"
        f"信号: {label}（来源: {source or '未标'} · "
        f"生成于 {datetime.now().isoformat(timespec='seconds')}）\n"
        f"提示: 本简报只生成本地文件，不发送任何内容。\n\n"
        f"══ 一、存活核查 ══\n{live_state} — {live_detail}\n\n"
        f"══ 二、面试准备 ══\n{prep_text}\n",
        encoding="utf-8")
    state.setdefault("generated", {})[key] = {
        "path": str(fp), "kind": kind,
        "at": datetime.now().isoformat(timespec="seconds"), "source": source,
    }
    _save_state(state)
    return fp


def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        st = _load_state().get("generated", {})
        print(f"🧭 已生成简报 {len(st)} 份：")
        for k, v in st.items():
            print(f"   {k} → {v.get('path')}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

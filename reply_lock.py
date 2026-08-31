#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REPLY_REVIEW_LOCK — Job Hunter 最高优先级交互规则（代码层，非 prompt 层）。

规则（2026-08-31 用户定稿 v5 第十二条）：
    回复 HR/Boss 的优先级 > 自动投递。
    生成回复可以自动化，发送回复必须人工确认。
    待审核回复存在期间，任何自动投递 worker 一律不得发送简历。

架构位置：Scheduler / Orchestrator 层的确定性锁。
LLM 只负责理解上下文和起草回复；本模块负责"暂停投递、排队审核、
严格语义确认、发送、释放、恢复"——模型抽风也绕不过文件锁。

锁 = data/reply_review.lock（存在即锁定）
队列 = data/reply_pending.json（每条会话：HR原文/上下文/拟回复/目的/状态）
断点 = data/queue_checkpoint.json（投递轮被暂停时保存，恢复时跳过已完成组合）
统计 = data/reply_stats.json（与投递额度统计完全独立，四套计数）

确认语义（整句精确匹配，规范化后）：
    ✅ 白名单：发送 / 确认发送 / 可以发 / 发吧 / 确认 / 同意发送 / 发出去
    ❌ 其余一切（嗯/看看/可以/还行/行/应该可以…）均不视为确认，保持等待。
    超时不自动发送。默认不是同意。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).parent
DATA_DIR = SKILL_DIR / "data"
LOCK_FILE = DATA_DIR / "reply_review.lock"
PENDING_FILE = DATA_DIR / "reply_pending.json"
CHECKPOINT_FILE = DATA_DIR / "queue_checkpoint.json"
STATS_FILE = DATA_DIR / "reply_stats.json"

# ── 严格确认语义：整句规范化后精确匹配白名单 ──
_CONFIRM_WORDS = {"发送", "确认发送", "可以发", "发吧", "确认", "同意发送", "发出去"}
_NOT_CONFIRM = {"嗯", "看看", "可以", "还行", "行", "没问题吧", "应该没问题",
                "应该可以", "好", "好吧", "ok", "OK", "哦", "了解"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm(text: str) -> str:
    t = (text or "").strip().strip("。.！!～~，, ")
    return t


def is_confirmation(text: str) -> bool:
    """只有白名单指令才算确认。语义不明确 → False（保持等待，绝不默认同意）。"""
    t = _norm(text)
    if t in _NOT_CONFIRM:
        return False
    return t in _CONFIRM_WORDS


# ── 锁 ──

def acquire(sessions: list, reason: str = "HR会话待人工审核") -> bool:
    """上锁 + 写入待审核队列。返回是否真的新上了锁（已有锁时合并队列不覆盖）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = _load_json(PENDING_FILE, [])
    known = {(s.get("company", ""), s.get("hr_name", "")) for s in existing}
    merged = existing + [s for s in sessions
                         if (s.get("company", ""), s.get("hr_name", "")) not in known]
    _dump(PENDING_FILE, merged)
    lock = _load_json(LOCK_FILE, None) or {
        "locked_at": _now(), "reason": reason, "source": "reply_lock",
    }
    _dump(LOCK_FILE, lock)
    record("hr_messages_detected", len(sessions))
    record("hr_replies_drafted", sum(1 for s in sessions if s.get("draft")))
    return True


def release_if_empty() -> bool:
    """队列清空 → 释放锁。返回是否释放了锁。"""
    remaining = [s for s in _load_json(PENDING_FILE, [])
                 if s.get("status") in ("pending", "edited", "approved")]
    if not remaining and LOCK_FILE.exists():
        LOCK_FILE.unlink()
        return True
    return False


def lock_info() -> dict:
    """锁状态 + 待审核摘要。投递 worker 每轮检查用。"""
    if not LOCK_FILE.exists():
        return {"locked": False}
    info = _load_json(LOCK_FILE, {}) or {}
    pend = [s for s in _load_json(PENDING_FILE, []) if s.get("status") == "pending"]
    info["locked"] = True
    info["pending_count"] = len(pend)
    return info


def is_locked() -> bool:
    return LOCK_FILE.exists()


# ── 审核队列操作 ──

def pending() -> list:
    return _load_json(PENDING_FILE, [])


def review_text() -> str:
    """人话审核卡：HR原消息 / 上下文 / 拟发送 / 目的。"""
    items = [s for s in pending() if s.get("status") == "pending"]
    if not items:
        return "📭 没有待审核的 HR 回复。"
    out = [f"⏸️ 投递已暂停（REPLY_REVIEW_LOCK）。{len(items)} 条回复等你审核：\n"]
    for s in items:
        out.append(f"─── [{s['id']}] {s.get('company','?')} · {s.get('hr_name','HR')} · {s.get('job','')}")
        out.append(f"   HR原消息: {s.get('hr_message','(未截取)')}")
        if s.get("context"):
            out.append(f"   上下文: {s['context']}")
        out.append(f"   拟发送: {s.get('draft','(草稿生成失败，请手填)')}")
        out.append(f"   目的: {s.get('purpose','回应HR问题')}")
        out.append("")
    out.append("回复方式：『确认 <id>』或『发送 <id>』=批准；『拒绝 <id>』=不发送；"
               "『修改 <id> 新内容』=重写后再审。未明确确认一律不发。")
    return "\n".join(out)


def _find(sid: str) -> dict:
    for s in pending():
        if str(s.get("id")) == str(sid):
            return s
    return {}


def _update(sid: str, **changes) -> bool:
    """在盘上列表里定位条目并应用变更，整体落盘。"""
    items = _load_json(PENDING_FILE, [])
    hit = False
    for s in items:
        if str(s.get("id")) == str(sid):
            s.update(changes)
            hit = True
    _dump(PENDING_FILE, items)
    return hit


def approve(sid: str, confirm_phrase: str) -> tuple[bool, str]:
    """只有白名单确认语才批准。批准后仍需显式 send。"""
    if not is_confirmation(confirm_phrase):
        return False, f"『{confirm_phrase}』不是明确确认指令，保持等待（白名单：{'/'.join(sorted(_CONFIRM_WORDS))}）"
    if not _find(sid):
        return False, f"未找到待审核条目 {sid}"
    _update(sid, status="approved", confirmed_at=_now())
    record("hr_replies_confirmed")
    return True, f"[{sid}] 已批准，待发送"


def reject(sid: str, note: str = "") -> tuple[bool, str]:
    if not _find(sid):
        return False, f"未找到待审核条目 {sid}"
    changes = {"status": "rejected", "rejected_at": _now()}
    if note:
        changes["reject_note"] = note
    _update(sid, **changes)
    if release_if_empty():
        return True, f"[{sid}] 已拒绝，队列清空，锁释放 → 可恢复投递"
    return True, f"[{sid}] 已拒绝（不发送）"


def edit_draft(sid: str, new_text: str) -> tuple[bool, str]:
    if not _find(sid):
        return False, f"未找到待审核条目 {sid}"
    _update(sid, draft=new_text, status="pending")  # 修改后重新进入待审核，改完≠确认
    return True, f"[{sid}] 草稿已更新，仍需明确确认才发送"


def send_approved(sid: str) -> tuple[bool, str]:
    """发送已批准的回复。未批准 → 拒绝执行（锁死『未经确认不得发送』）。"""
    s = _find(sid)
    if not s:
        return False, f"未找到条目 {sid}"
    if s.get("status") != "approved":
        return False, f"[{sid}] 状态={s.get('status','pending')}，未经明确确认，禁止发送"
    ok, msg = _do_send(s)
    if ok:
        _update(sid, status="sent", sent_at=_now())
        record("hr_replies_sent")
        release_if_empty()
    return ok, msg


def send_all_approved() -> tuple[int, int]:
    """批量发送全部已批准项（条间仍走保守间隔）。返回 (成功数, 失败数)。"""
    ok_n = fail_n = 0
    for s in [x for x in pending() if x.get("status") == "approved"]:
        ok, _ = send_approved(s["id"])
        ok_n += ok
        fail_n += (not ok)
        if ok_n + fail_n < 20:
            import time, random
            time.sleep(random.uniform(75, 105))
    return ok_n, fail_n


def _do_send(s: dict) -> tuple[bool, str]:
    """真实发送复用 hr_auto_reply.send_one（唯一出口，审计留痕）。"""
    if os.environ.get("REPLY_LOCK_FAKE_SEND") == "1":  # 测试钩子，不碰真实账号
        return True, "fake-send ok"
    try:
        sys.path.insert(0, str(SKILL_DIR))
        from hr_auto_reply import send_one
    except Exception as e:
        return False, f"发送器不可用: {e}"
    name_box = s.get("name_box") or ((s.get("hr_name") or "") + (s.get("company") or ""))
    try:
        ok = bool(send_one(None, name_box, s.get("draft", "")))
    except Exception as e:
        return False, f"发送异常: {str(e)[:80]}"
    return (True, "已发送") if ok else (False, "发送失败（找不到会话/被拦截）")


# ── 投递队列断点（暂停/恢复，绝不重新初始化整轮）──

def save_checkpoint(state: dict) -> None:
    """投递 worker 被锁暂停时调用：记录剩余队列位置。"""
    state = dict(state)
    state["saved_at"] = _now()
    _dump(CHECKPOINT_FILE, state)


def load_checkpoint() -> dict:
    cp = _load_json(CHECKPOINT_FILE, None)
    return cp or {}


def clear_checkpoint() -> None:
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()


# ── 双轨统计（与投递额度完全独立）──

_DEFAULT_STATS = {"hr_messages_detected": 0, "hr_replies_drafted": 0,
                  "hr_replies_confirmed": 0, "hr_replies_sent": 0}


def record(kind: str, n: int = 1) -> None:
    st = dict(_load_json(STATS_FILE, {}))
    st[kind] = int(st.get(kind, 0)) + n
    st["updated_at"] = _now()
    _dump(STATS_FILE, st)


def stats() -> dict:
    return {**_DEFAULT_STATS, **_load_json(STATS_FILE, {})}


# ── 内部 ──

def _save_all() -> None:
    _dump(PENDING_FILE, pending())


def _load_json(p: Path, default):
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _dump(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ── CLI ──

def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        info = lock_info()
        if not info.get("locked"):
            print("🔓 无待审核回复，投递可正常进行。")
        else:
            print(f"🔒 REPLY_REVIEW_LOCK 生效（{info.get('locked_at','?')}）"
                  f"待审核 {info.get('pending_count',0)} 条 — 自动投递已全部暂停")
            print(review_text())
        st = stats()
        print("\n📈 HR回复统计（独立于投递额度）: "
              + " / ".join(f"{k.replace('hr_','')}={v}" for k, v in st.items()
                           if not k.startswith("updated")))
    elif cmd == "review":
        print(review_text())
    elif cmd == "confirm" and len(sys.argv) >= 3:
        phrase = sys.argv[3] if len(sys.argv) > 3 else "确认"
        ok, msg = approve(sys.argv[2], phrase)
        print(("✅ " if ok else "⛔ ") + msg)
    elif cmd == "reject" and len(sys.argv) >= 3:
        ok, msg = reject(sys.argv[2], " ".join(sys.argv[3:]))
        print(("✅ " if ok else "⛔ ") + msg)
    elif cmd == "send" and len(sys.argv) >= 3:
        ok, msg = send_approved(sys.argv[2])
        print(("✅ " if ok else "⛔ ") + msg)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

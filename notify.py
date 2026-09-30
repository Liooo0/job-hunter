#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""告警触达 — 出事了必须有人知道（2026-09-15 新增，2026-09-24 升级飞书通道，2026-09-28 改走 job-notify 独立应用）

── 为什么有这个文件 ──
在此之前 job-hunter 全仓库没有任何通知代码：触发 kill switch、撞上验证码、
连续 UNCERTAIN 提前收工、登录失败进入睡眠——**全部是静默的**，只有你主动去翻
日志才知道当天投递窗口已经浪费。2026-08-12 的封号就是这么吃的亏。

── 设计约束 ──
1. **绝不阻断投递**：任何异常都在内部吞掉，返回 False，调用方不检查返回值也能跑。
2. **零依赖**：只用标准库 urllib / subprocess，跑在评分引擎同款纯标准库约束下。
3. **凭据不进仓库**：job-hunter 是公开仓库。凭据优先读环境变量，缺失时才回落到
   本机凭据文件（~/.job-notify-identity.json），路径在仓库之外。
4. **防轰炸**：同一个 key 默认 30 分钟内只推一次（多次验证码/多次收工不重复轰炸），
   状态存 data/alert_state.json。可用 JOBHUNTER_ALERT_MIN_INTERVAL 覆盖。
5. **通道优雅降级**：飞书 (lark-cli bot，job-notify 独立应用) → 微信 (Hermes iLink) → WxPusher 备用。
   ⚠️ 飞书发送必须显式 `--profile job-notify`：lark-cli 不指定 profile 会走 active profile
   （本机 = rental-mgmt 波比管家）→ 求职消息串进甲方项目专用窗口（2026-09-28 修正）。
6. **可关闭**：JOBHUNTER_ALERT_DISABLED=1 完全静默（含不写状态文件）。

用法：
    from notify import alert
    alert("captcha", "Boss 出现安全验证", "正文…")     # 冷却 30 分钟
    alert("kill_switch", "连续失败自动熔断", "…", throttle=0)  # 不冷却，必推

命令行（run_daily.sh 收工摘要用）：
    python3 notify.py "标题" "正文"
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "data" / "alert_state.json"
LOG_FILE = BASE / "data" / "logs" / "alerts.log"

WXPUSHER_SEND = "https://wxpusher.zjiecode.com/api/send/message"
# 凭据来源（都在仓库之外，公开仓库里不会出现 token/uid）：
#   ① 环境变量 → ② 本项目的 .env.local（gitignored）→ ③ 本机凭据文件
LOCAL_ENV = Path(__file__).resolve().parent / ".env.local"
FALLBACK_ENV = Path.home() / ".hermes" / "credentials" / "bobi.env"
# 飞书 / 微信凭据文件：仓库之外
HERMES_ENV = Path.home() / ".hermes" / ".env"
# 求职通知专用飞书应用身份（job-notify profile）。open_id 按应用隔离——
# 此文件里的 ou_ 只在 job-notify 应用下有效。绝不借用 rental-mgmt（波比管家）身份文件，
# 那会把求职消息串进甲方项目专用窗口（2026-09-28 修正的串台事故）。
FEISHU_IDENTITY_FILE = Path.home() / ".job-notify-identity.json"
FEISHU_PROFILE = "job-notify"   # lark-cli 发送身份；可用 JOBHUNTER_FEISHU_PROFILE 覆盖

DEFAULT_THROTTLE_SEC = 1800  # 30 分钟

# 各告警级别的前缀，推送标题里一眼看出严重程度
LEVEL_ICON = {"info": "ℹ️", "warn": "⚠️", "error": "🛑"}


def _read_env_file(path: Path, token: str, uid: str) -> tuple:
    """从 .env 风格文件里补全缺失的 token/uid。已存在的值不覆盖。"""
    try:
        if not path.exists():
            return token, uid
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "WXPUSHER_APP_TOKEN" and not token:
                token = v
            elif k == "WXPUSHER_UID" and not uid:
                uid = v
    except Exception:
        pass
    return token, uid


def _load_credentials() -> tuple:
    """返回 (app_token, uid) 用于 WxPusher 兜底。"""
    token = os.environ.get("WXPUSHER_APP_TOKEN", "").strip()
    uid = os.environ.get("WXPUSHER_UID", "").strip()
    if not token or not uid:
        token, uid = _read_env_file(LOCAL_ENV, token, uid)
    if not token or not uid:
        token, uid = _read_env_file(FALLBACK_ENV, token, uid)
    return token, uid


def _log(line: str) -> None:
    """告警自身的留痕，永远不抛异常（告警失败不能反过来搞挂投递）。"""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%F %T')}] {line}\n")
    except Exception:
        pass


def _should_send(key: str, throttle: int) -> bool:
    """节流判断：同 key 在 throttle 秒内只推一次。throttle<=0 表示不节流。纯判断，不写状态。"""
    if throttle <= 0:
        return True
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    except Exception:
        state = {}
    now = time.time()
    last = float(state.get(key, 0) or 0)
    if now - last < throttle:
        return False
    return True


def _record_sent(key: str) -> None:
    """记录发送成功时间戳用于后续节流。仅在渠道实际发送成功后调用。"""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    except Exception:
        state = {}
    state[key] = time.time()
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _feishu_target() -> str:
    """获取飞书 open_id (ou_xxx)。优先从环境变量，缺失从本机非追踪配置读取。

    ⚠️ open_id 按应用隔离：必须是 **job-notify 应用**下的 open_id。绝不拿
    rental-mgmt / Hermes / 其它应用的 open_id——要么 `open_id cross app` 拒发，
    要么借道别人的应用把求职消息串进不相干的窗口（2026-09-28 修正）。
    """
    t = os.environ.get("JOBHUNTER_FEISHU_TARGET", "").strip()
    if t:
        return t
    try:
        if FEISHU_IDENTITY_FILE.exists():
            d = json.loads(FEISHU_IDENTITY_FILE.read_text(encoding="utf-8"))
            oid = d.get("admin_open_id") or (d.get("allowed_open_ids", [None])[0] if d.get("allowed_open_ids") else "")
            if oid:
                return oid
    except Exception:
        pass
    return ""


def _send_feishu(title: str, content: str) -> bool:
    """走飞书 (lark-cli) 实时推送 —— 用求职专用应用 job-notify 的 bot 身份。

    必须显式 `--profile job-notify`：不指定时 lark-cli 走 active profile
    （本机 = rental-mgmt 波比管家）→ 求职消息串进甲方项目专用窗口（2026-09-28 修正）。
    """
    target = _feishu_target()
    if not target:
        _log("[feishu-skip] 没读到飞书 open_id (JOBHUNTER_FEISHU_TARGET / ~/.job-notify-identity.json 均空)")
        return False

    profile = os.environ.get("JOBHUNTER_FEISHU_PROFILE", "").strip() or FEISHU_PROFILE
    hermes_lark = Path.home() / ".hermes" / "node" / "bin" / "lark-cli"
    exe = str(hermes_lark) if hermes_lark.exists() else os.environ.get("JOBHUNTER_LARK_BIN", "lark-cli")

    env = os.environ.copy()
    home = str(Path.home())
    env["HERMES_HOME"] = os.path.join(home, ".hermes")
    env["PATH"] = f"{home}/.hermes/node/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")

    msg_body = f"{title}\n\n{content}".strip()
    try:
        proc = subprocess.run(
            [exe, "--profile", profile, "im", "+messages-send", "--user-id", target, "--text", msg_body, "--as", "bot"],
            capture_output=True, text=True, timeout=30, env=env
        )
        if proc.returncode == 0:
            _log(f"[feishu-sent] {title}")
            return True
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        _log(f"[feishu-failed] {title} | rc={proc.returncode} | {err}")
        return False
    except Exception as e:
        _log(f"[feishu-exception] {title} | {e}")
        return False


def _weixin_target() -> str:
    """微信投递目标，形如 weixin:o9cq...@im.wechat。"""
    t = os.environ.get("JOBHUNTER_WEIXIN_TARGET", "").strip()
    if t:
        return t if t.startswith("weixin:") else f"weixin:{t}"
    try:
        if HERMES_ENV.exists():
            for raw in HERMES_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
                k, _, v = raw.strip().partition("=")
                if k.strip() == "WEIXIN_ALLOWED_USERS" and v.strip():
                    uid = v.strip().split(",")[0].strip()
                    if uid:
                        return f"weixin:{uid}"
    except Exception:
        pass
    return ""


def _send_weixin(title: str, content: str) -> bool:
    """走微信（iLink）实时推送 —— 复用 Hermes 网关的通道。"""
    target = _weixin_target()
    if not target:
        _log("[weixin-skip] 没读到微信 uid（JOBHUNTER_WEIXIN_TARGET / WEIXIN_ALLOWED_USERS 都空）")
        return False
    exe = os.environ.get("JOBHUNTER_HERMES_BIN", "hermes")
    try:
        proc = subprocess.run(
            [exe, "send", "--to", target, "--subject", title, "--quiet", content],
            capture_output=True, text=True, timeout=45,
        )
        if proc.returncode == 0:
            _log(f"[weixin-sent] {title}")
            return True
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        _log(f"[weixin-failed] {title} | rc={proc.returncode} | {err}")
        return False
    except Exception as e:
        _log(f"[weixin-exception] {title} | {e}")
        return False


def _send_wxpusher(key: str, full_title: str, body: str) -> bool:
    """备用通道：WxPusher（推给显式配置的 uid，禁止群发）。"""
    token, uid = _load_credentials()
    if not token or not uid:
        _log(f"[no-credential] {key} | {full_title}（无 wxpusher 凭据，跳过）")
        return False

    payload = json.dumps({
        "appToken": token,
        "content": f"**{full_title}**\n\n{body}",
        "contentType": 3,          # 3 = markdown
        "uids": [uid],
        "summary": full_title[:100],
    }, ensure_ascii=False).encode("utf-8")

    try:
        req = urllib.request.Request(
            WXPUSHER_SEND, data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") == 1000:
            _log(f"[sent] {key} | {full_title}")
            return True
        _log(f"[failed] {key} | {full_title} | {data.get('msg', data)}")
        return False
    except urllib.error.URLError as e:
        _log(f"[network] {key} | {full_title} | {e}")
        return False
    except Exception as e:
        _log(f"[exception] {key} | {full_title} | {e}")
        return False


def alert(key: str, title: str, content: str = "", level: str = "warn",
          throttle: int = DEFAULT_THROTTLE_SEC) -> bool:
    """推一条告警。返回是否发送成功（调用方可以不看）。

    通道顺序：**飞书 (优先) → 微信 (备用) → WxPusher (兜底)**。任一成功即算成功；
    全失败只写日志，绝不抛异常（告警不能反过来搞挂投递）。

    key      : 告警类型标识，用于节流（如 'captcha' / 'kill_switch' / 'login'）
    title    : 标题（推送到飞书/微信的摘要行）
    content  : 正文
    level    : info / warn / error，影响标题前缀图标
    throttle : 同 key 节流秒数，0 = 每次都推
    """
    if os.environ.get("JOBHUNTER_ALERT_DISABLED", "") == "1":
        _log(f"[disabled] {key} | {title}")
        return False
    env_throttle = os.environ.get("JOBHUNTER_ALERT_MIN_INTERVAL", "")
    if env_throttle.isdigit():
        throttle = int(env_throttle)

    if not _should_send(key, throttle):
        _log(f"[throttled] {key} | {title}")
        return False

    icon = LEVEL_ICON.get(level, "⚠️")
    full_title = f"{icon} job-hunter · {title}"
    body = content.strip()
    if body:
        body += "\n\n"
    body += f"⏰ {datetime.now().strftime('%m-%d %H:%M')}"

    channel = os.environ.get("JOBHUNTER_ALERT_CHANNEL", "feishu").strip().lower()

    sent = False
    try:
        # 1. 优先飞书通道
        if channel in ("feishu", "both", "all"):
            try:
                if _send_feishu(full_title, body):
                    sent = True
            except Exception as e:
                _log(f"[feishu-exception] {key} | {e}")
            if not sent:
                _log(f"[fallback] {key} | 飞书通道失败，尝试备用通道")

        # 2. 次选微信通道
        if not sent and (channel in ("weixin", "both", "all") or channel == "feishu"):
            try:
                if _send_weixin(full_title, body):
                    sent = True
            except Exception as e:
                _log(f"[weixin-exception] {key} | {e}")
            if not sent:
                _log(f"[fallback] {key} | 微信通道失败，改走 wxpusher")

        # 3. 兜底 WxPusher
        if not sent:
            try:
                if _send_wxpusher(key, full_title, body):
                    sent = True
            except Exception as e:
                _log(f"[wxpusher-exception] {key} | {e}")
    except Exception as e:
        _log(f"[alert-top-exception] {key} | {e}")
        sent = False

    if sent:
        _record_sent(key)
    return sent


def main() -> int:
    """命令行入口：python3 notify.py "标题" "正文" """
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 notify.py <标题> [正文]")
        return 2
    title = sys.argv[1]
    content = sys.argv[2] if len(sys.argv) > 2 else ""
    ok = alert(f"cli:{title[:20]}", title, content, level="info", throttle=0)
    print(f"推送{'成功' if ok else '未发出（见 data/logs/alerts.log）'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

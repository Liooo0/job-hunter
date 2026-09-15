#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""告警触达 — 出事了必须有人知道（2026-09-15 新增）

── 为什么有这个文件 ──
在此之前 job-hunter 全仓库没有任何通知代码：触发 kill switch、撞上验证码、
连续 UNCERTAIN 提前收工、登录失败进入睡眠——**全部是静默的**，只有你主动去翻
日志才知道当天投递窗口已经浪费。2026-08-12 的封号就是这么吃的亏。

── 设计约束 ──
1. **绝不阻断投递**：任何异常都在内部吞掉，返回 False，调用方不检查返回值也能跑。
2. **零依赖**：只用标准库 urllib（不引入 requests），跑在评分引擎同款纯标准库约束下。
3. **凭据不进仓库**：job-hunter 是公开仓库。凭据优先读环境变量，缺失时才回落到
   本机其它项目已有的 .env（weather-api-backend 的 wxpusher 凭据），路径在仓库之外。
4. **防轰炸**：同一个 key 默认 30 分钟内只推一次（多次验证码/多次收工不重复轰炸），
   状态存 data/alert_state.json。可用 JOBHUNTER_ALERT_MIN_INTERVAL 覆盖。
5. **可关闭**：JOBHUNTER_ALERT_DISABLED=1 完全静默（含不写状态文件）。

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
#   ① 环境变量 → ② 本项目的 .env.local（gitignored）→ ③ 本机其它项目已有的 .env
LOCAL_ENV = Path(__file__).resolve().parent / ".env.local"
FALLBACK_ENV = Path.home() / "weather-api-backend" / ".env"
# 微信（iLink）通道凭据：Hermes 网关自己的 .env，仓库之外
HERMES_ENV = Path.home() / ".hermes" / ".env"

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
    """返回 (app_token, uid)。

    优先级：环境变量 → 本项目 .env.local → 本机 weather-api-backend/.env。
    **绝不自动拉取订阅者列表群发**：那个 app 的订阅者里有别人，把你求职告警
    推给他们就是泄露隐私（与 95fen-monitor 的同一条规矩）。拿不到 uid 就只写日志、
    不推送。
    """
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
    """节流判断：同 key 在 throttle 秒内只推一次。throttle<=0 表示不节流。"""
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
    state[key] = now
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return True


def _weixin_target() -> str:
    """微信投递目标，形如 weixin:o9cq...@im.wechat。

    优先级：环境变量 JOBHUNTER_WEIXIN_TARGET → Hermes 网关的 WEIXIN_ALLOWED_USERS。
    **只认显式配置的 uid**，绝不群发（与 wxpusher 那条同样的红线）。
    """
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
    """走微信（iLink）实时推送 —— 复用 Hermes 网关的通道，不用另配一套凭据。

    注意 iLink 侧的限流熔断阈值是 1（限流一次即冷却 30s），所以告警是
    低频率事件才适合走这条路；失败就降级到 wxpusher，绝不重试轰炸。
    """
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


def alert(key: str, title: str, content: str = "", level: str = "warn",
          throttle: int = DEFAULT_THROTTLE_SEC) -> bool:
    """推一条告警。返回是否发送成功（调用方可以不看）。

    通道顺序：**微信（真实时推送）→ wxpusher 备用**。任一成功即算成功；
    全失败只写日志，绝不抛异常（告警不能反过来搞挂投递）。

    key      : 告警类型标识，用于节流（如 'captcha' / 'kill_switch' / 'login'）
    title    : 标题（推送到微信的摘要行）
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

    channel = os.environ.get("JOBHUNTER_ALERT_CHANNEL", "weixin").strip().lower()

    if channel in ("weixin", "both") and _send_weixin(full_title, body):
        return True
    if channel == "weixin":
        # 微信挂了就自动降级到 wxpusher，不要把告警丢进黑洞
        _log(f"[fallback] {key} | 微信通道失败，改走 wxpusher")

    return _send_wxpusher(key, full_title, body)


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

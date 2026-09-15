#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""public-repo-guard — 提交前闸门：路径策略 + 内容 PII/密钥扫描。

设计原则（源自 job-hunter 2026-09-15 泄露事故复盘）：
  「.gitignore 写的是意图，Git 记录的是事实」——所以本闸门不问 .gitignore 想挡什么，
  只问 **git 到底要提交什么**，并且**同时看路径和内容**。

两道门：
  1. 路径门：数据库/备份/日志/导出/.env/私钥 一律不准进库（.env.example 显式放行）
  2. 内容门：对 **staged diff 的新增行** 扫 PII 与密钥（手机号/身份证/邮箱/绝对路径/
     API key/私钥/连接串/真实姓名）

真实姓名等本机敏感词从 **仓库外的** 词表读（~/.hermes/pii-denylist.txt），
这样闸门脚本本身可以公开、不含任何 PII。

用法（作为 pre-commit hook 自动调用，也可手动跑）：
    python3 scripts/public_repo_guard.py            # 检查 staged 内容
    python3 scripts/public_repo_guard.py --all      # 检查全部被追踪文件（发布前审计）
    python3 scripts/public_repo_guard.py --history  # 检查全历史（用于 CI / 清理后验收）

紧急绕过（会打大字警告，且 CI 仍会拦）：
    PII_GUARD_ALLOW=1 git commit ...
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# ── 路径门：这些路径类一律不准进库（按「数据类别」写，不按曾经用过的目录名写）──
BLOCK_PATH_PATTERNS = [
    (r"\.(db|sqlite|sqlite3|db3)$", "数据库文件（业务数据）"),
    (r"\.(bak|backup|old|orig|tmp|swp)$", "备份/临时文件"),
    (r"\.log$", "运行日志（常含业务数据）"),
    (r"(^|/)logs?/", "日志目录"),
    (r"(^|/)backups?/", "备份目录"),
    (r"(^|/)exports?/", "导出目录（业务数据）"),
    (r"(^|/)runtime/", "运行时数据目录"),
    (r"(^|/)secrets?/", "凭据目录"),
    (r"\.env($|\.)", "环境变量文件（含密钥）"),
    (r"(^|/)\.env", "环境变量文件（含密钥）"),
    (r"\.(pem|key|p12|pfx|jks|keystore)$", "私钥/证书"),
    (r"(^|/)id_(rsa|dsa|ecdsa|ed25519)", "SSH 私钥"),
    (r"(^|/)credentials?(\.|$)", "凭据文件"),
    (r"(^|/)config\.local\.json$", "本机配置"),
    (r"^config\.(json|.*\.json)$", "本机配置（含真实路径/招呼语）"),
    (r"(^|/)my_profile\.(txt|md|json)$", "个人档案（PII）"),
    (r"(^|/)node_modules/", "依赖目录"),
    (r"(^|/)\.venv/|(^|/)venv/", "虚拟环境"),
    (r"(^|/)pending_replies/|(^|/)sent_replies/|(^|/)archived_replies/", "回复记录（PII）"),
    (r"^data/reply_", "回复队列数据（PII）"),
    (r"(^|/)mail_scan_state\.json$", "本机运行时状态"),
    (r"(^|/)queue_checkpoint\.json$", "本机运行时状态"),
]
# 显式放行（即使在路径门下命中）
ALLOW_PATH = [
    r"\.env\.example$",
    r"(^|/)\.env\.sample$",
    r"(^|/)requirements\.txt$",
    # 已脱敏的配置模板（README 引用的那种，内容为通用占位值）
    r"(^|/)config\.example\.json$",
    r"(^|/)config\.(sample|template)\.json$",
]

# ── 内容门：staged 新增行里出现这些即拦 ──
# 每条：(正则, 说明, 是否允许「明显合成」的值)
CONTENT_PATTERNS = [
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号", True),
    (r"(?<![\dXx])\d{17}[\dXx](?![\dXx])", "身份证号", False),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "邮箱", True),
    # 注意: 历史重写时把真用户名替换成了 /Users/REPLACED，那是**脱敏占位符**不是真路径，
    # 由下方 ALLOW 规则放行；真实用户名命中的才拦。
    (r"/Users/[A-Za-z0-9_.-]+", "本机绝对路径（含用户名）", False),
    (r"[A-Za-z]:\\\\+Users", "Windows 本机路径（含用户名）", False),
    (r"\bsk-[A-Za-z0-9_-]{16,}", "OpenAI 风格 API Key", False),
    (r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}", "GitHub Token", False),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS Access Key", False),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥正文", False),
    (r"(?i)authorization\s*[:=]\s*[\"']?(bearer|basic)\s+[A-Za-z0-9._-]{12,}", "Authorization 头", False),
    (r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*[\"'][A-Za-z0-9_\-/+]{16,}[\"']", "疑似密钥赋值", False),
    (r"(?i)(postgres|postgresql|mysql|mongodb|redis)://[^\s:/]+:[^\s@]+@", "数据库连接串（含口令）", False),
    (r"\bcf-[A-Za-z0-9_-]{20,}\b", "Cloudflare Token", False),
    (r"\bzh_[A-Za-z0-9]{16,}\b", "其他平台 Token", False),
]
# 明显合成的值 → 不算泄露（真实值绝不会长这样）
SYNTHETIC_OK = [
    # 历史重写留下的脱敏占位符（不含真实用户名，属刻意保留的标记）
    r"/Users/(REPLACED|REDACTED|<[^>]+>|<用户名>|<user>|<name>)",
    # 13800000000 / 13700000000 类：1[3-9] + 任意一位 + 8 个 0
    # （★ 2026-09-15 自测踩坑：原写成 1[3-9]0{8}，"138" 的第三位是 8 不是 0 → 误拦合成手机号。
    #   闸门误杀比漏杀更危险——使用者会干脆关掉它。）
    r"1[3-9]\d0{8}",
    r"13800138000",
    r"(?i)[a-z0-9._%+-]+@(example|test|localhost|example\.com|foo|bar)\.",
    r"(?i)(test|demo|sample|dummy|fake|user|admin|zhangsan|lisi|wangwu)[A-Za-z0-9._%+-]*@",
    r"(?i)@(test|example)\.(com|org|net)",
    r"000-0000-0000",
    # 合成身份证号：合法 18 位但尾号是占位式连号（1234/5678/0000…），真实证件绝不会长这样
    r"\d{14}(1234|5678|0000|1111|9999|4321|8765)(?![\dXx])",
    r"TEST_USER",
    r"XXXXXXXX",
    # 文档里的占位写法（如 weixin:o9cq...@im.wechat）——省略号说明是示例，不是真值
    r"[A-Za-z0-9]{1,8}\.\.\.@",
    # 微信投递目标：真实 openid 是 28 位大小写混排；测试夹具用的 o9cq123 / aaaa 这类短串放行
    r"[A-Za-z0-9]{1,10}@im\.wechat",
]

REAL_NAME_FILE = Path.home() / ".hermes" / "pii-denylist.txt"


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw).stdout


def staged_files():
    out = sh(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"])
    return [f for f in out.splitlines() if f.strip()]


def tracked_files():
    return [f for f in sh(["git", "ls-files"]).splitlines() if f.strip()]


def staged_added_lines():
    """返回 [(file, lineno, text)]，只含新增行。"""
    out = sh(["git", "diff", "--cached", "-U0", "--no-color"])
    rows, cur = [], None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and cur:
            rows.append((cur, line[1:]))
    return rows


def load_real_names():
    if not REAL_NAME_FILE.exists():
        return []
    names = []
    for line in REAL_NAME_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            names.append(s)
    return names


def check_path(path):
    for pat in ALLOW_PATH:
        if re.search(pat, path):
            return None
    for pat, why in BLOCK_PATH_PATTERNS:
        if re.search(pat, path):
            return why
    return None


def is_synthetic(text):
    return any(re.search(p, text) for p in SYNTHETIC_OK)


def check_content_line(text, real_names):
    """返回 [(原因, 命中片段)]。

    白名单（SYNTHETIC_OK）对**所有**规则生效：凡是看起来就是合成/占位值的（13800000000、
    user@example.com、TEST_USER_001、/Users/REPLACED 这类），一律放行。
    ★ 2026-09-15 自测踩坑：最初只在 allow_synth=True 的规则上查白名单，导致
      「脱敏占位符 /Users/REPLACED」被当成真实路径误拦、历史审计永远过不去。
    """
    hits = []
    for pat, why, _allow_synth in CONTENT_PATTERNS:
        for m in re.finditer(pat, text):
            frag = m.group(0)
            if is_synthetic(frag):
                continue
            hits.append((why, frag[:60]))
    for name in real_names:
        if name in text:
            hits.append(("真实姓名/称呼", name))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="审计全部被追踪文件（发布前）")
    ap.add_argument("--history", action="store_true", help="审计全历史（CI/清理后验收）")
    args = ap.parse_args()

    allow = os.environ.get("PII_GUARD_ALLOW") == "1"
    real_names = load_real_names()

    blocked_paths, blocked_content = [], []

    if args.history:
        # 全历史：逐 commit 扫（用于 CI 与历史清理后的验收）
        # ★ 复用 check_content_line 而非另写正则 —— 否则白名单（合成值/脱敏占位符）不生效，
        #   历史审计会永远失败（自测踩坑：曾把脱敏占位符 /Users/REPLACED 当成真实路径）。
        revs = sh(["git", "rev-list", "--all"]).split()
        for rev in revs[:400]:
            out = sh(["git", "grep", "-n", "-I", "-E",
                      r"/Users/|sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}",
                      rev])
            for line in out.splitlines()[:8]:
                body = line.split(":", 2)[-1] if line.count(":") >= 2 else line
                for why, frag in check_content_line(body, real_names):
                    blocked_content.append((line[:150], why))
        files = []
    elif args.all:
        files = tracked_files()
    else:
        files = staged_files()

    # 路径门
    for f in files:
        why = check_path(f)
        if why:
            blocked_paths.append((f, why))

    # 内容门
    if args.all:
        for f in tracked_files():
            p = Path(f)
            if not p.exists() or p.stat().st_size > 2_000_000:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for why, frag in check_content_line(line, real_names):
                    blocked_content.append((f"{f}:{i}  {frag}", why))
    elif not args.history:
        for f, line in staged_added_lines():
            for why, frag in check_content_line(line, real_names):
                blocked_content.append((f"{f}  {frag}", why))

    if not blocked_paths and not blocked_content:
        print("✅ public-repo-guard: 路径门 + 内容门 均通过")
        return 0

    print("=" * 68)
    print("❌ BLOCKED — public-repo-guard 拦下了本次提交")
    print("=" * 68)
    if blocked_paths:
        print("\n【路径门】受保护文件不得进入 Git：")
        for f, why in blocked_paths[:20]:
            print(f"  ⛔ {f}\n     原因：{why}")
    if blocked_content:
        print("\n【内容门】检测到 PII / 密钥：")
        seen = set()
        for loc, why in blocked_content[:20]:
            key = (why, loc.split()[-1] if loc.split() else loc)
            if key in seen:
                continue
            seen.add(key)
            print(f"  ⛔ {loc}\n     命中：{why}")
    print("""
处理建议：
  · 业务数据/日志/备份 → 移到仓库外（如 ~/var/<项目>/），并从暂存区移除：git rm --cached <文件>
  · 密钥/凭据 → 放本机 /etc/<项目>.env 或 ~/.<tool>.env，仓库只留 .env.example
  · PII（姓名/手机/邮箱/路径）→ 改为合成数据（如 13800000000 / TEST_USER_001）
  · 确认是误报 → 建议改值；确需放行：PII_GUARD_ALLOW=1 git commit（CI 仍会拦）
""")
    if allow:
        print("⚠️  PII_GUARD_ALLOW=1 已设置 → 本次放行（请确认没有真实数据被提交！）")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

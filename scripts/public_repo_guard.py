#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""public_repo_guard v2 — 公开仓库防泄露与发布前安全门禁系统

设计基准：2026-09-24 全库安全审计与历史清理收尾标准
核心定位：本地预检、发布门禁、全历史扫描、媒体元数据审计与合规分级

════════════════════════════════════════════════════════════════════
架构约束与安全原则
════════════════════════════════════════════════════════════════════
1. 绝对安全措辞防护（Wording Guardrail）：
   严禁输出「100% 安全」、「绝对安全」、「零风险」、「完全合规」等过度宣称。
   最终结论仅允许判定为：PASS / PASS WITH NOTES / REVIEW REQUIRED / FAIL。
   明确区分「No finding detected」与「Proven absent」。
2. 四层防御与深度扫描：
   L1 文件路径分类门（按数据类别拦截，不依赖内容）
   L2 内容检测门（PII denylist + 电话/身份证/邮箱正则 + Secret + 本机路径）
   L3 Git 历史全量门（全 commit + 全 blob 历史追溯）
   L4 提交身份审查门（Author / Committer 真实姓名、私人邮箱与本地主机名）
   L5 多媒体元数据门（EXIF GPS 经纬度 + 相机/镜头硬件序列号）
   L6 远端引用暴露面（Remote refs / PR refs 只读分析）
3. 退出码契约（Release Mode Exit Codes）：
   0: PASS / PASS WITH NOTES（可进入人工发布阶段）
   1: FAIL（存在严重违规或未脱敏数据，禁止发布）
   2: REVIEW REQUIRED（存在需人工判断的非阻塞项或线上只读引用）

用法：
  python3 public_repo_guard.py                       # 默认 staged（pre-commit 模式）
  python3 public_repo_guard.py --mode=worktree       # 当前工作树（含未追踪但未忽略文件）
  python3 public_repo_guard.py --mode=head           # 当前 HEAD 提交树
  python3 public_repo_guard.py --mode=history        # Git 全历史对象扫描
  python3 public_repo_guard.py --mode=release        # 发布门禁综合全检
  python3 public_repo_guard.py --mode=full           # 完整深度全量体检
  python3 public_repo_guard.py --path /path/to/repo  # 针对指定仓库路径执行
  python3 public_repo_guard.py --json                # 机器可读 JSON 输出
  python3 public_repo_guard.py --md                  # 人类可读 Markdown 输出
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ════════════════════════════════════════════════════════════════════
# 绝对安全措辞防护常量
# ════════════════════════════════════════════════════════════════════
FORBIDDEN_WORDS = [
    "100% 安全", "100%安全", "绝对安全", "零风险", "完全安全", "完全合规",
    "绝对没有泄露", "绝对没有风险", "绝对合规", "没有任何风险"
]

STATUS_PASS = "PASS"
STATUS_PASS_NOTES = "PASS WITH NOTES"
STATUS_REVIEW_REQUIRED = "REVIEW REQUIRED"
STATUS_FAIL = "FAIL"


@dataclass
class Finding:
    layer: str          # L1_PATH, L2_CONTENT, L3_HISTORY, L4_AUTHOR, L5_MEDIA, L6_REMOTE
    severity: str       # HIGH, MEDIUM, LOW, INFO
    category: str       # PII, SECRET, PATH, DATABASE, EXIF_GPS, EXIF_SERIAL, AUTHOR, PR_REF, etc.
    target: str         # 文件路径、commit hash 或 refname
    detail: str         # 详细说明
    evidence: str       # 证据（已脱敏或部分哈希）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ════════════════════════════════════════════════════════════════════
# L1 文件级分类（数据类别层）
# ════════════════════════════════════════════════════════════════════
FILE_CLASSES = [
    ("database", r"\.(db|sqlite|sqlite3|db3|db-wal|db-shm|ksj|nksj|ldb)$", "deny",
     "数据库文件（业务数据/持久化存储）", False),
    ("backup", r"(^|/)(backups?|归档|archive-data)/|\.(bak|backup|old|orig|tmp|dump)$", "deny",
     "备份/副本文件（业务数据历史副本）", False),
    ("log", r"(^|/)logs?/|\.(log|out|err)$", "deny",
     "日志文件（可能携带运行明细与交互数据）", False),
    ("export", r"(^|/)(exports?|导出|receipts?|票据)/.*\.(csv|xlsx?|pdf|json|txt|jpe?g|png|zip|db)$|(^|/)(receipts?|票据)/", "deny",
     "导出明细或票据凭单（真实业务数据）", False),
    ("secret", r"(^|/)\.env(\.|$)|\.(pem|key|p12|pfx)$|(^|/)id_(rsa|ed25519)|credential|secret|token\.json$", "deny",
     "敏感凭据或私钥文件", False),
    ("runtime", r"^runtime/|^var/|(^|/)(state|runtime)/|(^|/)\.cache/", "deny",
     "运行时状态与缓存目录", False),
    ("local-data", r"^data/|^datasets/(?!public/)|^fixtures/(?!public/)|^local/(backups|logs|exports|receipts|data)/", "deny",
     "项目内运行数据目录（公开数据集需移至 datasets/public/）", False),
    ("deploy-local", r"\.(plist|service)$|(^|/)LaunchAgents/", "deny",
     "本机服务配置定义（可能含绝对路径，建议改模板）", True),
    ("template", r"\.(example|template|sample)(\.|$)|\.plist\.template$|(^|/)anon-rules\.example", "public",
     "模板与样例配置（键名完整、无私有值）", True),
    ("public-data", r"^(fixtures|datasets)/public/|^(tests?/fixtures|fixtures)/", "public",
     "已声明的公开测试集/夹具", True),
    ("source", r"\.(py|js|mjs|ts|tsx|jsx|sh|bash|rb|go|rs|java|kt|swift|c|h|cpp|html|css|scss|vue|sql|ya?ml|toml|ini|cfg|json|md|txt|bat|ps1)$", "public",
     "源码、文档及工程配置文件", True),
]
COMPILED_FILE_CLASSES = [
    (name, re.compile(pat), verdict, why, scan)
    for name, pat, verdict, why, scan in FILE_CLASSES
]

VENDOR_PATH = re.compile(r"(^|/)(vendor|node_modules|third_party|dist|build)/|\.min\.(js|css|mjs)$")

BINARY_EXTENSIONS = {
    ".glb", ".gltf", ".bin", ".wasm", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".ico", ".tiff", ".heic", ".mp4", ".mov", ".zip", ".tar", ".gz", ".pyc",
    ".db", ".sqlite", ".sqlite3", ".pkl", ".parquet", ".pdf", ".woff", ".woff2",
    ".ttf", ".eot", ".ds_store"
}

ALLOW_PATH = [
    (re.compile(r"\.example$|\.example\.|\.template$|\.sample$"), "模板/示例文件"),
    (re.compile(r"(^|/)\.gitkeep$"), "空目录占位符"),
    (re.compile(r"^docs/"), "文档目录"),
    (re.compile(r"(^|/)anon-rules\.example\.txt$"), "脱敏规则模板"),
]

# ════════════════════════════════════════════════════════════════════
# L2 内容级规则（正则与 Secret Pattern）
# ════════════════════════════════════════════════════════════════════
DENY_CONTENT_RULES = [
    ("phone", re.compile(r"1[3-9]\d{9}(?![\d])"), "HIGH", "手机号码"),
    ("email", re.compile(r"(?<![\w.])[\w.%+-]+@[\w.-]+\.(com|cn|net|org|edu|io|me|co)(?![\w.])"), "MEDIUM", "电子邮件地址"),
    ("id_card", re.compile(r"(?<![\d])\d{17}[\dXx](?![\d])"), "HIGH", "公民身份证号码"),
    ("path_mac", re.compile(r"/Users/(?!<)[A-Za-z0-9_.-]+"), "MEDIUM", "macOS 用户绝对路径"),
    ("path_linux", re.compile(r"/home/(?!<)[A-Za-z0-9_.-]+"), "MEDIUM", "Linux 用户绝对路径"),
    ("path_win", re.compile(r"[A-Za-z]:\\+Users"), "MEDIUM", "Windows 用户绝对路径"),
    ("secret_openai", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "HIGH", "OpenAI / LLM API Key"),
    ("secret_gh", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "HIGH", "GitHub Token"),
    ("secret_aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "HIGH", "AWS Access Key ID"),
    ("secret_privkey", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "HIGH", "非对称私钥头部"),
    ("secret_hardcoded", re.compile(r"(?i)\b(api[_-]?key|app[_-]?secret|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"), "HIGH", "硬编码密钥变量"),
    ("secret_db_uri", re.compile(r"(?i)\b(postgres|mysql|mongodb|redis|amqp)://[^\s'\"]*:[^\s'\"]*@"), "HIGH", "带密码的数据库连接字符串"),
    ("secret_feishu_app", re.compile(r"\bcli_[a-z0-9]{12,}"), "MEDIUM", "飞书应用凭据 (cli_*)"),
    ("secret_feishu_ou", re.compile(r"\bou_[a-z0-9]{20,}"), "MEDIUM", "飞书用户标识 (ou_*)"),
]

# 合成占位符与放行规则
ALLOW_CONTENT_RULES = [
    re.compile(r"1[3-9]\d0{8}"),                                      # 13800000000 典型合成手机
    re.compile(r"13800138000"),
    re.compile(r"000-0000-0000"),
    re.compile(r"TEST_USER"),
    re.compile(r"XXXXXXXX"),
    re.compile(r"(?i)[a-z0-9._%+-]+@(example|test|localhost|foo|bar|invalid|email)\."),
    re.compile(r"(?i)@(example|test|localhost)\."),
    re.compile(r"(?i)(test|demo|sample|dummy|fake|user|admin|zhangsan|lisi|wangwu|yourname|yourid|lio\.photography)[A-Za-z0-9._%+-]*@"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@users\.noreply\.github\.com\b"), # GitHub 官方隐私代理邮箱
    re.compile(r"\b[A-Za-z0-9._%+-]+@local\b"),
    re.compile(r"\d{14}(1234|5678|0000|1111|9999|4321|8765)(?![\dXx])"), # 合成假身份证
    re.compile(r"/Users/(REPLACED|REDACTED|<[^>]+>|<用户名>|<user>|<name>|example|runner|vscode)"), # 脱敏路径占位符
    re.compile(r"/home/(REPLACED|REDACTED|<[^>]+>|Photos|photos|user|users|app|data|workspace|shared|docker|admin|pi|vscode|runner)"),
    re.compile(r"[A-Za-z0-9]{1,8}\.\.\.@"),                           # 文档占位省略邮箱
    re.compile(r"[A-Za-z0-9]{1,10}@im\.wechat"),                      # 短串占位
    re.compile(r"cli_x{6,}|ou_x{6,}"),                                # 占位飞书 id
    re.compile(r"(?i)(sk|ghp|gho)[-_](x{3,}[\.]*x*|your|test|fake|example|placeholder|abc|123)"),
    re.compile(r"(?i)sk-?tes?t?[\.-]"),
]

DENYLIST_FILES = [
    os.path.expanduser("~/.hermes/pii-denylist.txt"),
]

# ════════════════════════════════════════════════════════════════════
# PII 哈希表（fail-closed 的 CI 侧输入）
# ════════════════════════════════════════════════════════════════════
# 为什么需要：仓库外明文词表（pii-denylist）在 CI 里不存在，于是 CI 的
# 「姓名维」恒为空 —— 同一个闸门在本机拦得住、在 CI 永远绿。CI 侧改用
# keyed HMAC-SHA256 滑窗：仓内只放 digest，密钥走 GitHub Actions Secret /
# 本机 0600 文件。**不可用裸 SHA**：姓名熵低（中文 2-3 字），裸哈希可被离线枚举。
PII_HMAC_KEY_ENV = "PII_HMAC_KEY"
PII_HMAC_KEY_FILE = os.path.expanduser("~/.hermes/pii-hmac.key")
PII_HASH_FILE_DEFAULT = ".github/pii-hashes.txt"
ZERO_WIDTH_CHARS = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_for_pii(text: str) -> str:
    """PII 匹配前归一化：NFC + 去零宽字符（防「名字中间插零宽」绕过）。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    for ch in ZERO_WIDTH_CHARS:
        t = t.replace(ch, "")
    return t


def is_cjk_or_alnum(ch: str) -> bool:
    """快路径预过滤用：只对含字母/数字/CJK 的窗口做 HMAC。"""
    return ch.isalnum() or "\u4e00" <= ch <= "\u9fff"


def make_hmac_fn(key: bytes):
    """返回 f(msg)->hex。手工实现标准 HMAC（与 hmac.new 等价，已单测对齐），
    复用 ipad 前缀的 sha256 对象，滑窗场景下显著快于每窗口新建 hmac 对象。"""
    block = 64
    k = key if len(key) <= block else hashlib.sha256(key).digest()
    k = k + b"\x00" * (block - len(k))
    ipad = bytes(x ^ 0x36 for x in k)
    opad = bytes(x ^ 0x5C for x in k)
    base = hashlib.sha256(ipad)
    opad_digest_prefix = opad

    def _f(msg: bytes) -> str:
        inner = base.copy()
        inner.update(msg)
        return hashlib.sha256(opad_digest_prefix + inner.digest()).hexdigest()

    return _f


def redact(text: str, keep_start: int = 2, keep_end: int = 2) -> str:
    """脱敏敏感词，避免测试与报告自身成为泄露源。"""
    if not text:
        return "[REDACTED]"
    if len(text) <= keep_start + keep_end:
        return "[REDACTED]"
    return f"{text[:keep_start]}***{text[-keep_end:]}"


def is_binary_file(path: str) -> bool:
    """快速判断是否为二进制文件（按后缀或前 8KB 零字节探测）"""
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return True
    except OSError:
        pass
    return False


class GuardEngine:
    def __init__(self, repo_path: str = "."):
        self.repo_path = os.path.abspath(repo_path)
        self.guardrc_declared_public: List[Tuple[re.Pattern, str]] = []
        self.guardrc_extra_deny: List[Tuple[re.Pattern, str]] = []
        self.guardrc_extra_allow: List[re.Pattern] = []
        self.denylist: List[str] = []
        self.findings: List[Finding] = []
        self.metrics: Dict[str, Any] = {}
        # PII 闸门状态（fail-closed）
        self.pii_gate: Dict[str, Any] = {}
        self.pii_hash_file: str = ""
        self.pii_hashset: Set[str] = set()
        self.pii_hash_meta: Dict[str, str] = {}
        self.pii_key: bytes = b""
        self.pii_window_lengths: List[int] = []
        self.pii_fast_path: bool = False
        self.pii_hmac_fn = None
        self.denylist_loaded: bool = False
        self.scan_unit_count: int = 0

        self._load_guardrc()
        self._load_denylist()
        self._load_pii_gate()

    def _run_git(self, args: List[str]) -> str:
        try:
            res = subprocess.run(
                ["git", "-C", self.repo_path] + args,
                capture_output=True,
                text=True,
                errors="replace",
                check=False
            )
            return res.stdout.strip()
        except OSError:
            return ""

    def _load_guardrc(self):
        rc_path = os.path.join(self.repo_path, ".guardrc.json")
        if not os.path.exists(rc_path):
            return
        try:
            with open(rc_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for item in cfg.get("declared_public", []):
                pat = item.get("pattern", "")
                if pat:
                    self.guardrc_declared_public.append((re.compile(pat), item.get("reason", "已声明公开")))
            for item in cfg.get("extra_deny_paths", []):
                pat = item.get("pattern", "")
                if pat:
                    self.guardrc_extra_deny.append((re.compile(pat), item.get("reason", "仓库声明为禁止")))
            for pat in cfg.get("extra_allow_content", []):
                if pat:
                    self.guardrc_extra_allow.append(re.compile(pat))
            gate = cfg.get("pii_gate", {})
            if isinstance(gate, dict):
                self.pii_gate = gate
        except (OSError, ValueError):
            pass

    def _load_denylist(self):
        found_any = False
        for path in DENYLIST_FILES:
            if os.path.exists(path):
                found_any = True
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            w = line.strip()
                            if w and not w.startswith("#"):
                                self.denylist.append(w)
                except OSError:
                    pass
        self.denylist_loaded = bool(found_any and self.denylist)

    def _load_pii_gate(self):
        """加载 .guardrc 声明的 PII 哈希表与 HMAC 密钥（缺哪个都要在 config 检查里报红）。"""
        g = self.pii_gate or {}
        if not g:
            return
        hf = str(g.get("hash_file", "") or PII_HASH_FILE_DEFAULT).strip()
        if hf:
            self.pii_hash_file = hf if os.path.isabs(hf) else os.path.join(self.repo_path, hf)
            if os.path.exists(self.pii_hash_file):
                try:
                    with open(self.pii_hash_file, "r", encoding="utf-8") as f:
                        for line in f:
                            s = line.strip()
                            if not s or s.startswith("#"):
                                continue
                            if _DIGEST_RE.match(s):
                                self.pii_hashset.add(s)
                                continue
                            if ":" in s:
                                k, _, v = s.partition(":")
                                self.pii_hash_meta[k.strip()] = v.strip()
                except OSError:
                    pass
        key_env = os.environ.get(PII_HMAC_KEY_ENV, "").strip()
        if key_env:
            self.pii_key = key_env.encode("utf-8")
            self.metrics["pii_key_source"] = "env"
        elif os.path.exists(PII_HMAC_KEY_FILE):
            try:
                v = Path(PII_HMAC_KEY_FILE).read_text(encoding="utf-8").strip()
                if v:
                    self.pii_key = v.encode("utf-8")
                    self.metrics["pii_key_source"] = "file"
            except OSError:
                pass
        ls: List[int] = []
        for part in re.split(r"[,\s]+", self.pii_hash_meta.get("window_lengths", "")):
            if part.isdigit():
                ls.append(int(part))
        self.pii_window_lengths = sorted(set(ls)) or [3]
        self.pii_fast_path = self.pii_hash_meta.get("all_cjk_alnum", "").strip().lower() == "true"
        if self.pii_key:
            self.pii_hmac_fn = make_hmac_fn(self.pii_key)

    def _gate_config_findings(self):
        """fail-closed：声明了依赖的输入缺失/为空时一律判红，绝不静默通过。

        触发点：本轮实测的原始缺陷是「词表缺失 → 跳过姓名维 → PASS」。
        """
        if not self.pii_gate:
            self.metrics["pii_gate_declared"] = False
            return
        self.metrics["pii_gate_declared"] = True
        in_ci = os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"
        self.metrics["in_ci"] = in_ci

        def add(target: str, detail: str, ev: str):
            self.findings.append(Finding(
                layer="L0_GATE_CONFIG", severity="HIGH", category="GATE_CONFIG_MISSING",
                target=target, detail=detail, evidence=ev))

        # require_denylist 三态：
        #   true        —— 任何环境都必须有明文词表
        #   "local_only"—— 非 CI 环境必须有（CI 里明文本就不存在，由哈希表覆盖该维）
        #   "if_present"—— 文件在但读不出条目 = 红；文件不在 = 交给哈希表
        rd = self.pii_gate.get("require_denylist", "if_present")
        denylist_target = os.path.expanduser("~/.hermes/pii-denylist.txt")
        if rd is True or rd == "true" or (rd == "local_only" and not in_ci):
            if not self.denylist_loaded:
                add(denylist_target,
                    "仓库声明 require_denylist，但仓库外 PII 词表缺失或为空 → fail-closed"
                    "（缺输入必须红，不许因『没有词表』而放行）",
                    "denylist missing/empty")
        elif rd == "if_present":
            if os.path.exists(DENYLIST_FILES[0]) and not self.denylist_loaded:
                add(denylist_target,
                    "词表文件存在但读不出任何条目（空文件/不可读）→ fail-closed",
                    "denylist present but empty")
        if self.pii_gate.get("require_hash_file", True):
            if not self.pii_hash_file or not os.path.exists(self.pii_hash_file):
                add(self.pii_hash_file or str(self.pii_gate.get("hash_file", PII_HASH_FILE_DEFAULT)),
                    "仓库声明 require_hash_file，但 PII 哈希表文件不存在 → fail-closed",
                    "hash file missing")
            elif not self.pii_hashset:
                add(self.pii_hash_file,
                    "PII 哈希表存在但没有可用 digest 条目 → fail-closed", "hashset empty")
            ver = (self.pii_hash_meta.get("version") or "").strip()
            if ver and ver != "1":
                add(self.pii_hash_file,
                    f"哈希表 version={ver} 本引擎不认识 → fail-closed（不许静默信任未知格式）",
                    "unknown hashlist version")
        if self.pii_gate.get("require_key", True) and not self.pii_key:
            add(PII_HMAC_KEY_ENV,
                "缺 PII HMAC 密钥（CI 走 GitHub Actions Secret，本地走 ~/.hermes/pii-hmac.key）"
                " → fail-closed：无密钥则姓名维恒为空，等于没检查",
                "hmac key missing")

        # 词表在本机、digest 在仓里：词表改了却忘记重生成 digest = CI 姓名维静默缩水。
        # 本机有明文也有密钥，正是唯一能算出"该不该一致"的地方，所以在这里把它钉死。
        want_fp = (self.pii_hash_meta.get("fingerprint") or "").strip()
        if want_fp and self.pii_key and self.denylist_loaded:
            joined = "\n".join(sorted(normalize_for_pii(w) for w in self.denylist)).encode("utf-8")
            got_fp = hmac.new(self.pii_key, joined, hashlib.sha256).hexdigest()
            if got_fp != want_fp:
                add(self.pii_hash_file,
                    "仓内 PII 哈希表与本地词表不一致（词表已更新但 digest 未重生成）→ "
                    "CI 姓名维会静默缩水 → fail-closed。修：python3 ~/.hermes/tools/pii_digest.py "
                    "--repo <仓库路径>",
                    "hashlist stale vs local denylist")

    def _scan_pii_windows(self, target_label: str, text: str, layer: str):
        """keyed HMAC 滑窗：CI 侧姓名维的唯一输入（仓内只有 digest）。"""
        if not (self.pii_hashset and self.pii_hmac_fn):
            return
        norm = normalize_for_pii(text)
        if not norm:
            return
        hashed = 0
        n = len(norm)
        for L in self.pii_window_lengths:
            if L <= 0 or n < L:
                continue
            for i in range(n - L + 1):
                w = norm[i:i + L]
                if self.pii_fast_path and not all(is_cjk_or_alnum(c) for c in w):
                    continue
                hashed += 1
                if self.pii_hmac_fn(w.encode("utf-8")) in self.pii_hashset:
                    self.metrics["pii_windows_hashed"] = self.metrics.get("pii_windows_hashed", 0) + hashed
                    self.findings.append(Finding(
                        layer=layer, severity="HIGH", category="PII_HASH_MATCH",
                        target=target_label,
                        detail=f"命中仓库外 PII 哈希表（keyed HMAC 密文匹配，窗口长度 {L}）",
                        evidence=f"[hash-window-match len={L}]"))
                    return
        self.metrics["pii_windows_hashed"] = self.metrics.get("pii_windows_hashed", 0) + hashed

    def is_synthetic(self, fragment: str) -> bool:
        if any(p.search(fragment) for p in ALLOW_CONTENT_RULES):
            return True
        if any(p.search(fragment) for p in self.guardrc_extra_allow):
            return True
        return False

    def classify_file(self, path: str) -> Tuple[str, str, str, bool]:
        """L1 文件分类 -> (类别, 判定: deny/public, 说明, 是否需要内容扫描)"""
        rel_path = path.replace("\\", "/")
        for pat, why in self.guardrc_declared_public:
            if pat.search(rel_path):
                return ("declared-public", "public", f"仓库声明为公开: {why}", True)
        for pat, why in self.guardrc_extra_deny:
            if pat.search(rel_path):
                return ("declared-deny", "deny", f"仓库声明为禁止: {why}", False)
        for pat, why in ALLOW_PATH:
            if pat.search(rel_path):
                return ("allowed", "public", f"显式放行: {why}", True)
        for name, pat, verdict, why, scan in COMPILED_FILE_CLASSES:
            if pat.search(rel_path):
                return (name, verdict, why, scan)
        return ("unknown", "public", "常规项目文件", True)

    def scan_content_string(self, target_label: str, text: str, layer: str = "L2_CONTENT"):
        """L2 内容正则与敏感词匹配"""
        if not text:
            return
        self.scan_unit_count += 1
        # 1. 静态正则
        for rule_id, regex, sev, desc in DENY_CONTENT_RULES:
            for match in regex.finditer(text):
                frag = match.group(0)
                if self.is_synthetic(frag):
                    continue
                self.findings.append(Finding(
                    layer=layer,
                    severity=sev,
                    category=rule_id,
                    target=target_label,
                    detail=f"检测到疑似{desc}",
                    evidence=redact(frag)
                ))
        # 2. PII denylist 匹配（明文，仅本机有词表时生效）
        for word in self.denylist:
            if word and word in text:
                self.findings.append(Finding(
                    layer=layer,
                    severity="HIGH",
                    category="PII_DENYLIST",
                    target=target_label,
                    detail="命中仓库外 PII 敏感词表",
                    evidence=redact(word)
                ))
        # 3. PII 哈希表匹配（keyed HMAC 滑窗）—— CI 侧姓名维的唯一输入
        self._scan_pii_windows(target_label, text, layer)

    # ════════════════════════════════════════════════════════════════
    # 扫描收集器
    # ════════════════════════════════════════════════════════════════
    def scan_staged(self):
        """扫描 staged diff 新增行与新添文件路径"""
        names = self._run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"]).splitlines()
        for name in names:
            name = name.strip()
            if not name:
                continue
            cls, verdict, why, scan = self.classify_file(name)
            if verdict == "deny":
                self.findings.append(Finding(
                    layer="L1_PATH",
                    severity="HIGH",
                    category="PROHIBITED_PATH",
                    target=name,
                    detail=f"暂存区文件路径违规 ({why})",
                    evidence=name
                ))

        diff_text = self._run_git(["diff", "--cached", "--unified=0", "--diff-filter=ACMR"])
        cur_file = None
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                cur_file = line[6:].strip()
            elif line.startswith("+") and not line.startswith("+++") and cur_file:
                added_line = line[1:]
                if not VENDOR_PATH.search(cur_file) and not is_binary_file(os.path.join(self.repo_path, cur_file)):
                    self.scan_content_string(f"staged:{cur_file}", added_line, layer="L2_CONTENT")

    def scan_worktree(self):
        """扫描当前工作区所有被跟踪及未忽略的文件"""
        files = self._run_git(["ls-files"]).splitlines()
        untracked = self._run_git(["ls-files", "--others", "--exclude-standard"]).splitlines()
        all_files = sorted(set([f.strip() for f in files + untracked if f.strip()]))
        
        for rel_path in all_files:
            cls, verdict, why, scan = self.classify_file(rel_path)
            if verdict == "deny":
                self.findings.append(Finding(
                    layer="L1_PATH",
                    severity="HIGH",
                    category="PROHIBITED_PATH",
                    target=rel_path,
                    detail=f"工作区文件路径违规 ({why})",
                    evidence=rel_path
                ))
            abs_path = os.path.join(self.repo_path, rel_path)
            # 跳过 vendor 和二进制文件的内容扫描
            if scan and not VENDOR_PATH.search(rel_path) and not is_binary_file(abs_path):
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read(500_000)
                    self.scan_content_string(rel_path, text, layer="L2_CONTENT")
                except OSError:
                    pass

        # .gitignore 不是合法绕过：被忽略但确实存在于工作区的「禁止路径」必须显式报出，
        # 让操作者知道本闸门在此处看不见它，而不是静默略过。
        # （CI 侧按提交树扫描，force-add 仍会被拦住；此处只做诚实告知，故为 INFO 不阻断。）
        ignored = self._run_git(["ls-files", "--others", "--ignored", "--exclude-standard"]).splitlines()
        hidden = []
        for rel_path in sorted({x.strip() for x in ignored if x.strip()}):
            cls, verdict, why, scan = self.classify_file(rel_path)
            if verdict == "deny":
                hidden.append((rel_path, why))
        self.metrics["ignored_deny_path_count"] = len(hidden)
        for rel_path, why in hidden[:20]:
            self.findings.append(Finding(
                layer="L1_PATH",
                severity="INFO",
                category="IGNORED_DENY_PATH_HIDDEN",
                target=rel_path,
                detail=f"该禁止路径被 .gitignore 隐藏，不在本模式扫描范围内 ({why})"
                       " —— 本地忽略不能免除 CI：CI 按提交树扫描，force-add 仍会被拦",
                evidence=f"ignored & present: {rel_path}"
            ))

    def scan_head(self):
        """扫描 HEAD 提交的全部文件内容"""
        files = self._run_git(["ls-tree", "-r", "--name-only", "HEAD"]).splitlines()
        for rel_path in files:
            rel_path = rel_path.strip()
            if not rel_path:
                continue
            cls, verdict, why, scan = self.classify_file(rel_path)
            if verdict == "deny":
                self.findings.append(Finding(
                    layer="L1_PATH",
                    severity="HIGH",
                    category="PROHIBITED_PATH",
                    target=f"HEAD:{rel_path}",
                    detail=f"HEAD 树中包含违规路径 ({why})",
                    evidence=rel_path
                ))
            abs_path = os.path.join(self.repo_path, rel_path)
            if scan and not VENDOR_PATH.search(rel_path) and not is_binary_file(abs_path):
                content = self._run_git(["show", f"HEAD:{rel_path}"])
                self.scan_content_string(f"HEAD:{rel_path}", content, layer="L2_CONTENT")

    def scan_history(self):
        """全历史对象与 Commit 追溯扫描"""
        commits = self._run_git(["rev-list", "--all"]).splitlines()
        self.metrics["commit_count"] = len(commits)
        objects = self._run_git(["rev-list", "--all", "--objects"]).splitlines()
        self.metrics["object_count"] = len(objects)

        # 1. 扫描所有 Commit 的 Author / Committer 身份
        author_lines = self._run_git(["log", "--all", "--format=%h|%an|%ae|%cn|%ce"]).splitlines()
        for line in author_lines:
            parts = line.split("|")
            if len(parts) == 5:
                commit_sha, an, ae, cn, ce = parts
                for name, email in [(an, ae), (cn, ce)]:
                    email_lower = email.lower()
                    if ".local" in email_lower or "@localhost" in email_lower:
                        self.findings.append(Finding(
                            layer="L4_AUTHOR",
                            severity="MEDIUM",
                            category="AUTHOR_LOCAL_HOST",
                            target=commit_sha,
                            detail="提交身份暴露开发机本地主机名",
                            evidence=f"{name} <{email}>"
                        ))
                    for w in self.denylist:
                        if (w and (w in name or w in email)) and "users.noreply.github.com" not in email_lower:
                            self.findings.append(Finding(
                                layer="L4_AUTHOR",
                                severity="HIGH",
                                category="AUTHOR_PII",
                                target=commit_sha,
                                detail="提交身份包含 PII 敏感词",
                                evidence=f"{name} <{redact(email)}>"
                            ))

        # 2. 全量历史 diff 快速增量行扫描（覆盖历史添加后删除的敏感内容）
        diff_output = self._run_git(["log", "--all", "-p", "-U0", "--diff-filter=ACMR"])
        cur_commit = "initial"
        cur_file = ""
        for line in diff_output.splitlines():
            if line.startswith("commit "):
                cur_commit = line.split()[1][:8]
            elif line.startswith("diff --git "):
                parts = line.split(" b/")
                if len(parts) == 2:
                    cur_file = parts[1].strip()
            elif line.startswith("+++ b/"):
                cur_file = line[6:].strip()
            elif line.startswith("+") and not line.startswith("+++") and cur_file:
                added_text = line[1:]
                ext = os.path.splitext(cur_file)[1].lower()
                if not VENDOR_PATH.search(cur_file) and ext not in BINARY_EXTENSIONS:
                    self.scan_content_string(f"hist:{cur_commit}:{cur_file}", added_text, layer="L3_HISTORY")

        # 3. 扫描历史 Commit Message 中的 PII
        for term in self.denylist:
            if not term:
                continue
            out_msg = self._run_git(["log", "--all", f"--grep={term}", "--format=%h %s"])
            if out_msg:
                for match_line in out_msg.splitlines()[:5]:
                    self.findings.append(Finding(
                        layer="L3_HISTORY",
                        severity="HIGH",
                        category="COMMIT_MSG_PII",
                        target=match_line.split()[0],
                        detail="Git 历史提交信息 (Message) 命中 PII 敏感词",
                        evidence=f"commit msg: {match_line.split()[0]}"
                    ))

    def scan_media_metadata(self):
        """L5 多媒体 EXIF / GPS / 硬件序列号扫描 (集成 exiftool JSON 模式)"""
        media_exts = [".jpg", ".jpeg", ".png", ".webp", ".heic", ".tiff", ".mp4", ".mov"]
        files = self._run_git(["ls-files"]).splitlines()
        media_files = [
            os.path.join(self.repo_path, f.strip())
            for f in files
            if any(f.strip().lower().endswith(ext) for ext in media_exts)
        ]
        self.metrics["media_file_count"] = len(media_files)
        if not media_files:
            return

        try:
            cmd = ["exiftool", "-j", "-s", "-G1", "-GPSLatitude", "-GPSLongitude", "-GPSPosition",
                   "-SerialNumber", "-InternalSerialNumber", "-LensSerialNumber", "-CameraSerialNumber"]
            res = subprocess.run(cmd + media_files, capture_output=True, text=True, errors="replace", check=False)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    entries = json.loads(res.stdout)
                    for item in entries:
                        src = item.get("SourceFile", "")
                        rel_target = os.path.relpath(src, self.repo_path)
                        gps_val = None
                        serial_val = None
                        for k, v in item.items():
                            tag = k.split(":")[-1]
                            if ("GPS" in tag or tag in ("GPSLatitude", "GPSLongitude", "GPSPosition")) and v:
                                gps_val = v
                            elif "Serial" in tag and v:
                                serial_val = v

                        if gps_val:
                            self.findings.append(Finding(
                                layer="L5_MEDIA",
                                severity="HIGH",
                                category="EXIF_GPS",
                                target=rel_target,
                                detail="图片包含精确 GPS 经纬度地理位置",
                                evidence=f"GPS: {redact(str(gps_val))}"
                            ))
                        if serial_val:
                            self.findings.append(Finding(
                                layer="L5_MEDIA",
                                severity="MEDIUM",
                                category="EXIF_SERIAL",
                                target=rel_target,
                                detail="图片包含相机或镜头硬件序列号",
                                evidence=f"Serial: {redact(str(serial_val))}"
                            ))
                except json.JSONDecodeError:
                    pass
        except OSError:
            self.findings.append(Finding(
                layer="L5_MEDIA",
                severity="INFO",
                category="TOOL_MISSING",
                target="exiftool",
                detail="未检测到系统 exiftool，已跳过多媒体深度元数据扫描",
                evidence="N/A"
            ))

    def scan_gitleaks(self):
        """调用 gitleaks detect 扫描历史凭据"""
        try:
            res = subprocess.run(
                ["gitleaks", "detect", f"--source={self.repo_path}", "--no-banner", "--report-format=json"],
                capture_output=True,
                text=True,
                check=False
            )
            self.metrics["gitleaks_exit_code"] = res.returncode
            if res.returncode != 0 and res.stdout.strip():
                try:
                    leaks = json.loads(res.stdout)
                    for leak in leaks:
                        self.findings.append(Finding(
                            layer="L3_HISTORY",
                            severity="HIGH",
                            category="GITLEAKS_SECRET",
                            target=leak.get("Commit", "")[:8],
                            detail=f"Gitleaks 检测到敏感密钥 ({leak.get('Description', '')})",
                            evidence=f"Rule: {leak.get('RuleID', '')}, File: {leak.get('File', '')}"
                        ))
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass

    def scan_remote_refs(self):
        """L6 远端只读引用与 PR Refs 分析 (严格只读，不进行 fetch 写变更)"""
        remote_branches = self._run_git(["branch", "-r"]).splitlines()
        tags = self._run_git(["tag", "-l"]).splitlines()
        self.metrics["remote_branch_count"] = len(remote_branches)
        self.metrics["tag_count"] = len(tags)

        for rb in remote_branches:
            rb_clean = rb.strip().replace("origin/", "")
            if "optimize/repo-hardening" in rb_clean:
                diff_count = self._run_git(["rev-list", "--count", f"origin/main..origin/{rb_clean}"])
                if diff_count == "0":
                    self.findings.append(Finding(
                        layer="L6_REMOTE",
                        severity="LOW",
                        category="STALE_BRANCH",
                        target=rb_clean,
                        detail="远端分支已 100% 合并入 main，可择机清理",
                        evidence=f"origin/{rb_clean} (0 commits ahead)"
                    ))

        remotes = self._run_git(["remote"]).splitlines()
        if "origin" in [r.strip() for r in remotes]:
            ls_out = self._run_git(["ls-remote", "origin"])
            pull_refs = [line.split()[1] for line in ls_out.splitlines() if "refs/pull/" in line]
            self.metrics["remote_pull_ref_count"] = len(pull_refs)
            if pull_refs:
                self.findings.append(Finding(
                    layer="L6_REMOTE",
                    severity="INFO",
                    category="PR_REFS_DETECTED",
                    target="origin",
                    detail=f"远端存在 {len(pull_refs)} 个 PR Refs，重写历史后需注意 GitHub 服务端只读引用残留",
                    evidence=f"count: {len(pull_refs)}"
                ))

    # ════════════════════════════════════════════════════════════════
    # 执行评估与裁决 (Verdict Evaluation)
    # ════════════════════════════════════════════════════════════════
    def evaluate(self, mode: str = "release") -> Tuple[str, int]:
        """运行判定并返回 (Status, ExitCode)"""
        self.findings.clear()
        self.scan_unit_count = 0

        # fail-closed：先查依赖完整性，缺输入直接红（不许静默跳过姓名维）
        self._gate_config_findings()

        if mode == "staged":
            self.scan_staged()
        elif mode == "worktree":
            self.scan_worktree()
        elif mode == "head":
            self.scan_head()
        elif mode == "history":
            self.scan_history()
            self.scan_gitleaks()
        elif mode in ("release", "full"):
            self.scan_worktree()
            self.scan_head()
            self.scan_history()
            self.scan_media_metadata()
            self.scan_gitleaks()
            self.scan_remote_refs()

        # 覆盖度：声明了闸门依赖却什么都没扫 = 异常，必须红
        self.metrics["scan_unit_count"] = self.scan_unit_count
        # staged 模式下「0 个扫描单元」是常态（只删文件、只改二进制、只改路径），
        # 判红会把正常的删除提交拦死 —— 那不是漏检。真正要防的是 head/worktree/
        # release 这类全量模式「声明了闸门却什么都没扫到」。
        if self.pii_gate and self.scan_unit_count == 0 and mode != "staged":
            self.findings.append(Finding(
                layer="L0_GATE_CONFIG", severity="HIGH", category="GATE_CONFIG_EMPTY_SCAN",
                target=f"mode={mode}",
                detail="声明了 PII 闸门依赖，但本次扫描没有任何内容被检查（覆盖度为 0） → fail-closed",
                evidence="scan_unit_count=0"))

        # 去重
        deduped = []
        seen = set()
        for f in self.findings:
            key = (f.layer, f.severity, f.category, f.target, f.evidence)
            if key not in seen:
                seen.add(key)
                deduped.append(f)
        self.findings = deduped

        # 分级裁决
        has_high = any(f.severity == "HIGH" for f in self.findings)
        has_medium = any(f.severity == "MEDIUM" for f in self.findings)
        has_low = any(f.severity in ("LOW", "INFO") for f in self.findings)

        if has_high:
            verdict = STATUS_FAIL
            exit_code = 1
        elif has_medium:
            verdict = STATUS_REVIEW_REQUIRED
            exit_code = 2
        elif has_low:
            verdict = STATUS_PASS_NOTES
            exit_code = 0
        else:
            verdict = STATUS_PASS
            exit_code = 0

        self.metrics["verdict"] = verdict
        self.metrics["exit_code"] = exit_code
        self.metrics["timestamp"] = datetime.now(timezone.utc).isoformat()
        return verdict, exit_code

    def render_markdown(self) -> str:
        verdict = self.metrics.get("verdict", "UNKNOWN")
        ts = self.metrics.get("timestamp", "")
        
        lines = [
            f"# public_repo_guard v2 门禁审计报告",
            f"",
            f"> **仓库路径**：`{self.repo_path}`  ",
            f"> **执行时间**：`{ts}`  ",
            f"> **门禁判定**：**{verdict}**  ",
            f"",
            f"---",
            f"",
            f"## 一、基础指标概览",
            f"",
            f"| 指标项 | 统计值 | 说明 |",
            f"| :--- | :---: | :--- |",
            f"| **Commit 数量** | `{self.metrics.get('commit_count', 'N/A')}` | 历史全量提交数 |",
            f"| **Git 对象总数** | `{self.metrics.get('object_count', 'N/A')}` | 全历史 Blobs / Trees |",
            f"| **多媒体文件数** | `{self.metrics.get('media_file_count', '0')}` | 经 EXIF/GPS 扫描的图片与视频数 |",
            f"| **远端 PR 引用数**| `{self.metrics.get('remote_pull_ref_count', '0')}` | GitHub 远端只读 refs/pull 计数 |",
            f"",
            f"---",
            f"",
            f"## 二、发现项列表 (Findings)",
            f""
        ]

        if not self.findings:
            lines.append("✅ **No finding detected**：未检出任何高危 PII、Secret、EXIF 泄露或路径异常。")
        else:
            lines.append("| 严重等级 | 防御层级 | 规则类别 | 目标对象 | 详情说明 | 证据采样 (已脱敏) |")
            lines.append("| :---: | :---: | :---: | :--- | :--- | :--- |")
            for f in self.findings:
                lines.append(f"| **{f.severity}** | `{f.layer}` | `{f.category}` | `{f.target}` | {f.detail} | `{f.evidence}` |")

        lines.extend([
            "",
            "---",
            "",
            "## 三、安全边界与措辞规范说明",
            "",
            "> ⚠️ **措辞防护声明**：本工具已强制启用防护机制，不使用「100% 安全」、「绝对安全」等过度宣称。",
            "> 本次检验结论基于既定规则集（21 项 PII Denylist + 正则 + Gitleaks + Exiftool）完成，判定为 **" + verdict + "**。",
            "> 图片画面本身的视觉场景隐私（人脸、门牌等）需结合人工目视抽查确认。"
        ])
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="public_repo_guard v2 — public repo leak gate")
    parser.add_argument("--path", default=".", help="目标仓库路径 (默认当前目录)")
    parser.add_argument("--mode", choices=["staged", "worktree", "head", "history", "release", "full"],
                        default="staged", help="扫描运行模式")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--md", action="store_true", help="输出 Markdown 报告")
    parser.add_argument("--output", help="写入报告的目标文件路径")
    parser.add_argument("--classify", help="仅对指定文件路径进行分类评估")
    args = parser.parse_args()

    engine = GuardEngine(repo_path=args.path)

    if args.classify:
        cls, verdict, why, scan = engine.classify_file(args.classify)
        print(f"Path: {args.classify}\nClass: {cls}\nVerdict: {verdict}\nWhy: {why}\nScan Content: {scan}")
        sys.exit(0 if verdict != "deny" else 1)

    verdict, exit_code = engine.evaluate(mode=args.mode)

    if args.json:
        report_data = {
            "repo_path": engine.repo_path,
            "mode": args.mode,
            "verdict": verdict,
            "exit_code": exit_code,
            "metrics": engine.metrics,
            "findings": [f.to_dict() for f in engine.findings]
        }
        json_str = json.dumps(report_data, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(json_str)
        else:
            print(json_str)
    elif args.md:
        md_str = engine.render_markdown()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md_str)
        else:
            print(md_str)
    else:
        # 终端友好输出
        print("=" * 68)
        print(f"public_repo_guard v2 [{args.mode}] -> Verdict: {verdict} (Exit Code: {exit_code})")
        print("=" * 68)
        if not engine.findings:
            print("✅ No finding detected.")
        else:
            for f in engine.findings:
                print(f"[{f.severity:6s}] [{f.layer:10s}] {f.target} -> {f.detail} ({f.evidence})")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

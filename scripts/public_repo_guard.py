#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""public_repo_guard — 公开仓库防泄露体系（单例 Rule Engine）

来源：job-hunter 2026-09-15 个人信息泄露事故（真实 HR 对话/真名/本机路径被追踪并
推送公开仓库，持续 34 天无人发现）。核心教训：**.gitignore 写的是意图，Git 记录的是事实**。

════════════════════════════════════════════════════════════════════
架构约束（不可违反）
════════════════════════════════════════════════════════════════════

                     ┌──────────────────────────┐
                     │      Rule Engine         │  ← 唯一判定处
                     │  classify() + evaluate() │
                     └────────────┬─────────────┘
                                  │  所有模式都必须走这里
        ┌──────────┬──────────────┼──────────────┬───────────┐
     staged    worktree          tree           refs       history
   (pre-commit) (未提交/未追踪)  (被追踪全量)  (所有 ref)  (全历史 blob)

★ 规则：**任何调用方不得自行编写正则**。pre-commit / CI / 发布审计三者若各自维护
  一套检测逻辑，迟早分叉——2026-09-15 实测发生过：`--history` 分支另写了一套正则，
  导致合成值白名单在其中失效，历史审计永远失败。规则只在这里定义一次。

★ 规则：**检测规则可以公开，检测目标必须私有**。真实姓名/手机号等「检测目标」
  存在仓库外的词表里（~/.hermes/pii-denylist.txt），本脚本本身可安全公开。
  反例（真实发生过）：为了检测真名而把真名写进检测脚本 → 检测脚本自己成为泄露源；
  SECURITY.md 里引用真实 HR 称呼当例子 → 把刚清理的 PII 重新发布。

════════════════════════════════════════════════════════════════════
四层防线
════════════════════════════════════════════════════════════════════
  L1 文件级：按**类别**判断（runtime/backup/db/log/export/secret/deploy-local）
             → 路径本身就是违规，不必等内容里出现手机号
  L2 内容级：PII / secret / 本机绝对路径（staged diff 的新增行）
  L3 历史级：全 refs / 全历史 blob（HEAD 干净 ≠ 历史干净）
  L4 流程级：pre-commit(UX) → CI(边界) → branch protection(强制) → 发布审计

用法：
  python3 scripts/public_repo_guard.py                # staged（pre-commit 用）
  python3 scripts/public_repo_guard.py --worktree     # 工作区（含未追踪文件）
  python3 scripts/public_repo_guard.py --all          # 全部被追踪文件
  python3 scripts/public_repo_guard.py --refs         # 所有 ref 指向的内容
  python3 scripts/public_repo_guard.py --history      # 全历史每个 commit
  python3 scripts/public_repo_guard.py --classify X   # 只看某文件的分类判定
  python3 scripts/public_repo_guard.py --json         # 机器可读输出
退出码：0 通过 / 1 有违规（CI 与 pre-commit 均以此判断）
"""
import argparse
import json
import os
import re
import subprocess
import sys

# ════════════════════════════════════════════════════════════════════
# L1：文件级分类（数据分类层）
# ════════════════════════════════════════════════════════════════════
# 类别 → (正则列表, 判定, 说明, 是否还要做内容扫描)
#   判定: deny=禁止进入 Git / private=仅本机 / public=可公开
# 关键设计：**先分类，再判内容**。业务运行数据进 Git 是「类别错误」，
# 不该等它里面恰好出现手机号才拦（rental-mgmt 事故里 21 个文件就是这么漏的，
# 根因是 .gitignore 挡的路径与实际在用的路径不一致）。
FILE_CLASSES = [
    # ── 私有：绝不进 Git ──
    ("database",   r"\.(db|sqlite|sqlite3|db3|db-wal|db-shm|ksj|nksj|ldb)$", "deny",
     "数据库文件（业务数据）", False),
    ("backup",     r"(^|/)(backups?|归档|archive-data)/|\.(bak|backup|old|orig|tmp|dump)$", "deny",
     "备份/历史副本（业务数据）", False),
    ("log",        r"(^|/)logs?/|\.(log|out|err)$", "deny",
     "日志文件（可能携带运行明细与交互数据）", False),
    # 只拦**数据文件**：代码模块也可能叫 export/（实测 lio-erp 的
    # src/rent_expert/export/csv_excel.py 是导出功能代码，被误拦）。
    ("export",     r"(^|/)(exports?|导出|receipts?|票据)/.*\.(csv|xlsx?|pdf|json|txt|jpe?g|png|zip|db)$"
                   r"|(^|/)(receipts?|票据)/", "deny",
     "导出物/票据（真实业务数据）", False),
    ("secret",     r"(^|/)\.env(\.|$)|\.(pem|key|p12|pfx)$|(^|/)id_(rsa|ed25519)|credential|secret|token\.json$", "deny",
     "凭据/私钥（不得写入源码或仓库）", False),
    ("runtime",    r"^runtime/|^var/|(^|/)(state|runtime)/|(^|/)\.cache/", "deny",
     "运行数据目录", False),
    # ★ data/ 一律 deny：job-hunter 事故的敏感数据主来源就是 data/*.json
    #   （reply_pending.json 含真实 HR 对话）。曾因后缀 .json 被判成 source 放行——
    #   这是「类别错误」的典型：不该等内容里出现手机号才拦。
    #   公开数据集请放声明式命名空间 datasets/public/ 或 fixtures/public/。
    ("local-data", r"^data/|^datasets/(?!public/)|^fixtures/(?!public/)|^local/(backups|logs|exports|receipts|data)/", "deny",
     "项目内运行数据/导出目录（业务数据，公开物请放 datasets/public/）", False),
    # 只针对**本机服务定义**（launchd plist / systemd service）——它们必须含绝对路径，
    # 所以仓库里只允许放 __HOME__ 模板。不拦 *.conf：容器/应用配置本身可以公开，
    # 其中的绝对路径与凭据由 L2 内容门负责（自测踩坑：曾连 supervisord.conf 一起拦掉）。
    ("deploy-local", r"\.(plist|service)$|(^|/)LaunchAgents/", "deny",
     "本机服务定义（含绝对路径，应改 __HOME__ 模板）", True),

    # ── 允许公开（显式白名单，优先级最高）──
    ("template",   r"\.(example|template|sample)(\.|$)|\.plist\.template$|(^|/)anon-rules\.example", "public",
     "模板/示例（键名齐全、值为空或假值）", True),
    ("public-data", r"^(fixtures|datasets)/public/|^(tests?/fixtures|fixtures)/", "public",
     "已声明为公开数据集/测试夹具（须合成或公开信息，无 PII）", True),

    # ── 公开 ──
    ("source",     r"\.(py|js|mjs|ts|tsx|jsx|sh|bash|rb|go|rs|java|kt|swift|c|h|cpp|html|css|scss|vue|sql|ya?ml|toml|ini|cfg|json|md|txt|bat|ps1)$", "public",
     "源码/文档/配置模板", True),
]
FILE_CLASSES = [(name, re.compile(pat), verdict, why, scan) for name, pat, verdict, why, scan in FILE_CLASSES]

# 第三方代码/压缩产物：**跳过内容扫描**（里面的数字串会被误判成手机号/身份证，
# 实测 pdf.worker.min.js、three.module.js 大量误报——闸门噪音一多就会被无视）。
# 路径分类仍然生效（放错目录照样拦）。
VENDOR_PATH = re.compile(r"(^|/)(vendor|node_modules|third_party|dist|build)/|\.min\.(js|css|mjs)$")
SKIPPED_VENDOR = []

# 显式放行路径（优先于分类）：这些文件即使落在私有类别目录里也允许（且说明原因）
ALLOW_PATH = [
    (r"\.example$|\.example\.|\.template$|\.sample$", "模板/示例文件"),
    (r"(^|/)\.gitkeep$", "空目录占位标记（无内容）"),
    (r"^docs/", "文档目录"),
    (r"(^|/)anon-rules\.example\.txt$", "脱敏规则模板（纯假例子）"),
]

# ── 仓库级声明文件 .guardrc.json（随仓库走，review 可见）──
# 引擎保持通用，**例外由各仓库显式声明**，而不是把某项目的目录布局硬编码进引擎。
#   {
#     "declared_public": [{"pattern": "^data/public_page/", "reason": "静态页（无个人数据）"}],
#     "extra_deny_paths": [{"pattern": "^seed/", "reason": "..."}],
#     "extra_allow_content": ["PATTERN"]
#   }
def load_guardrc():
    import json as _json
    path = os.path.join(sh(["git", "rev-parse", "--show-toplevel" ]).strip() or ".", ".guardrc.json")
    if not os.path.exists(path):
        return [], [], []
    try:
        with open(path, encoding="utf-8") as f:
            cfg = _json.load(f)
    except (OSError, ValueError):
        return [], [], []
    pub = [(e.get("pattern", ""), e.get("reason", "已声明公开")) for e in cfg.get("declared_public", [])]
    deny = [(e.get("pattern", ""), e.get("reason", "仓库声明为禁止")) for e in cfg.get("extra_deny_paths", [])]
    allow = [re.compile(p) for p in cfg.get("extra_allow_content", []) if p]
    return pub, deny, allow

# ════════════════════════════════════════════════════════════════════
# L2：内容级规则（PII / secret / 绝对路径）
# ════════════════════════════════════════════════════════════════════
DENY_CONTENT = [
    (r"1[3-9]\d{9}(?![\d])", "手机号"),
    (r"(?<![\w.])[\w.%+-]+@[\w.-]+\.(com|cn|net|org|edu|io|me|co)(?![\w.])", "邮箱"),
    (r"(?<![\d])\d{17}[\dXx](?![\d])", "身份证号"),
    (r"/Users/[A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+", "本机绝对路径（含用户名）"),
    (r"[A-Za-z]:\\+Users", "Windows 本机路径（含用户名）"),
    (r"\bsk-[A-Za-z0-9_-]{16,}", "OpenAI 风格 API Key"),
    (r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}", "GitHub Token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS Access Key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
    (r"(?i)\b(api[_-]?key|app[_-]?secret|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]", "硬编码密钥"),
    (r"(?i)\b(postgres|mysql|mongodb|redis|amqp)://[^\s'\"]*:[^\s'\"]*@", "数据库连接串（含密码）"),
    (r"\bcli_[a-z0-9]{12,}", "飞书 App ID"),
    (r"\bou_[a-z0-9]{20,}", "飞书 Open ID"),
]

# 明显合成/占位的值 → 放行（真实值绝不会长这样）
# ★ 必须对所有规则生效。2026-09-15 实测坑：白名单曾只在部分规则上生效，
#   导致脱敏占位符 /Users/REPLACED 被当真实路径误拦、历史审计永远失败。
ALLOW_CONTENT = [
    r"1[3-9]\d0{8}",                                  # 13800000000 类（1[3-9]+任意位+8个0）
    r"13800138000",
    r"000-0000-0000",
    r"TEST_USER",
    r"XXXXXXXX",
    r"(?i)[a-z0-9._%+-]+@(example|test|localhost|foo|bar|invalid)\.",
    r"(?i)@(example|test|localhost)\.",
    r"(?i)(test|demo|sample|dummy|fake|user|admin|zhangsan|lisi|wangwu)[A-Za-z0-9._%+-]*@",
    r"\d{14}(1234|5678|0000|1111|9999|4321|8765)(?![\dXx])",   # 合成身份证（尾号占位连号）
    r"/Users/(REPLACED|REDACTED|<[^>]+>|<用户名>|<user>|<name>)",  # 历史重写留下的脱敏占位符
    r"/home/(REPLACED|REDACTED|<[^>]+>)",
    r"[A-Za-z0-9]{1,8}\.\.\.@",                       # 文档里的占位省略号
    r"[A-Za-z0-9]{1,10}@im\.wechat",                  # 短串微信 openid（真值 28 位）
    r"cli_x{6,}", r"ou_x{6,}",                        # 模板里的假 app_id / open_id
    r"(?i)(sk|ghp|gho)[-_](x{3,}[\.]*x*|your|test|fake|example|placeholder|abc|123)",
    r"(?i)sk-?tes?t?[\.-]",                     # 测试夹具里的 sk-tes...cdef
    r"/home/(Photos|photos|user|users|app|data|workspace|shared|docker|admin|pi|vscode|runner)",
]
ALLOW_CONTENT = [re.compile(p) for p in ALLOW_CONTENT]
DENY_CONTENT = [(re.compile(p), why) for p, why in DENY_CONTENT]

# 仓库外的敏感词表（检测目标私有）。不是所有项目都需要。
DENYLIST_CANDIDATES = [
    os.path.expanduser("~/.hermes/pii-denylist.txt"),
]


def load_real_names():
    """从仓库外词表读「不能公开出现的具体词」（真实姓名/称呼等）。词表缺省则跳过该检查。"""
    names = []
    for path in DENYLIST_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    w = line.strip()
                    if w and not w.startswith("#"):
                        names.append(w)
        except OSError:
            pass
    return names


_EXTRA_ALLOW = []          # 由 .guardrc.json 注入
_DECLARED_PUBLIC = []      # 由 .guardrc.json 注入
_EXTRA_DENY = []           # 由 .guardrc.json 注入


def is_synthetic(fragment):
    """是否明显合成/占位（对**所有**规则生效）。"""
    return any(p.search(fragment) for p in ALLOW_CONTENT) or \
        any(p.search(fragment) for p in _EXTRA_ALLOW)


def classify(path):
    """L1 文件分类 → (类别, 判定, 说明, 是否内容扫描)。

    优先级：仓库声明的公开目录 > 通用白名单 > 私有类别 > 公开类别
    """
    for pat, why in _DECLARED_PUBLIC:
        if re.search(pat, path):
            return ("declared-public", "public", f"仓库声明为公开：{why}", True)
    for pat, why in _EXTRA_DENY:
        if re.search(pat, path):
            return ("declared-deny", "deny", f"仓库声明为禁止：{why}", False)
    for pat, why in ALLOW_PATH:
        if re.search(pat, path):
            return ("allowed", "public", f"显式放行：{why}", True)
    for name, pat, verdict, why, scan in FILE_CLASSES:
        if pat.search(path):
            return (name, verdict, why, scan)
    return ("unknown", "public", "未分类（按内容判定）", True)


def scan_text(text, real_names):
    """L2 内容扫描 → [(原因, 命中片段)]。token 化的 word-boundary 走 Python 正则（\\w 含中文）。"""
    hits = []
    for pat, why in DENY_CONTENT:
        for m in re.finditer(pat, text):
            frag = m.group(0)
            if is_synthetic(frag):
                continue
            hits.append((why, frag[:60]))
    for w in real_names:
        if w and w in text:
            hits.append(("敏感词（来自仓库外词表）", w))
    return hits


class Violation:
    __slots__ = ("mode", "path", "reason", "fragment", "layer")

    def __init__(self, mode, path, reason, fragment="", layer="L1"):
        self.mode, self.path, self.reason, self.fragment, self.layer = mode, path, reason, fragment, layer

    def __str__(self):
        tail = f"  ({self.fragment})" if self.fragment else ""
        return f"  ⛔ [{self.layer}] {self.path} → {self.reason}{tail}"


# ════════════════════════════════════════════════════════════════════
# 唯一的判定入口：所有模式都走这里
# ════════════════════════════════════════════════════════════════════
def evaluate(mode, path, text, real_names):
    """对「某个路径 + 其内容」做完整判定 → [Violation]。
    L1 先分类：路径类别为 deny 时**直接违规，不再看内容**（类别错误）。
    """
    out = []
    cls, verdict, why, do_scan = classify(path)

    # 第三方代码/压缩产物：只判路径，不判内容
    if do_scan and path and VENDOR_PATH.search(path):
        do_scan = False
        SKIPPED_VENDOR.append(path)

    if verdict == "deny":
        out.append(Violation(mode, path, f"L1 路径类别违规：{why}", "", "L1"))
        # 路径已违规，内容还扫（信息更全）但不追加同类重复
    if not do_scan or text is None:
        return out
    for reason, frag in scan_text(text, real_names):
        out.append(Violation(mode, path, reason, frag, "L2"))
    return out


# ════════════════════════════════════════════════════════════════════
# 各模式：只负责「取数据」，判定一律交给 evaluate
# ════════════════════════════════════════════════════════════════════
def sh(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, errors="replace").stdout
    except OSError:
        return ""


def read_text_file(path, limit=400_000):
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None            # 二进制 → 跳过内容扫描（路径类别仍然生效）


def collect_staged():
    """pre-commit：只看本次提交实际要写进 Git 的东西（staged diff 的新增行）。"""
    items = []
    names = sh(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]).split()
    diff = sh(["git", "diff", "--cached", "--unified=0", "--diff-filter=ACMR"])
    cur, buf = None, []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            if cur is not None:
                items.append((cur, "\n".join(buf)))
            cur, buf = line[6:], []
        elif line.startswith("+") and not line.startswith("+++"):
            buf.append(line[1:])
    if cur is not None:
        items.append((cur, "\n".join(buf)))
    seen = {p for p, _ in items}
    items += [(n, None) for n in names if n not in seen]     # 二进制/空 diff：仍做路径判定
    return items


def collect_worktree():
    """工作区：被追踪 + **未被忽略的**未追踪文件。

    为什么不含被忽略的文件：被正确忽略的运行数据（如 data/app.db）本来就该在本机存在，
    判它违规是假阳性——「文件存在」不是问题，「文件进了 Git」才是。
    真正危险的是 `git add -A` 会一口气收进去的那批：未追踪且未被忽略。
    （★ 自测踩坑：最初把 --ignored 也算进来，本机必然失败、CI 必然通过，闸门等于废掉。）
    """
    items = []
    for p in sh(["git", "ls-files"]).split():
        items.append((p, read_text_file(p)))
    for p in sh(["git", "ls-files", "--others", "--exclude-standard"]).split():
        items.append((p, read_text_file(p)))
    return items


def collect_tree():
    """全部被追踪文件。"""
    items = []
    for p in sh(["git", "ls-files"]).split():
        items.append((p, read_text_file(p)))
    return items


def collect_refs():
    """所有 ref（分支 + tag）指向的树。"""
    items = []
    refs = sh(["git", "for-each-ref", "--format=%(refname)"]).split()
    for ref in refs:
        for p in sh(["git", "ls-tree", "-r", "--name-only", ref]).split():
            items.append((f"{ref}:{p}", None))    # 路径判定；内容由 history 负责
    return items


def collect_history(max_commits=400):
    """全历史：每个 commit 里被添加/修改的内容（HEAD 干净 ≠ 历史干净）。"""
    items = []
    for rev in sh(["git", "rev-list", "--all"]).split()[:max_commits]:
        out = sh(["git", "grep", "-n", "-I", "-E",
                  r"/Users/|/home/|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|"
                  r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                  r"cli_[a-z0-9]{12,}|ou_[a-z0-9]{20,}|1[3-9]\d{9}", rev])
        for line in out.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            items.append((f"{rev[:8]}:{parts[1]}", parts[2]))
    return items


MODES = {
    "staged": collect_staged,
    "worktree": collect_worktree,
    "tree": collect_tree,
    "refs": collect_refs,
    "history": collect_history,
}


def main():
    ap = argparse.ArgumentParser(description="public_repo_guard — public repo leak gate")
    ap.add_argument("--all", action="store_true", help="全部被追踪文件")
    ap.add_argument("--worktree", action="store_true", help="工作区（含未追踪）")
    ap.add_argument("--refs", action="store_true", help="所有 ref 指向的内容")
    ap.add_argument("--history", action="store_true", help="全历史")
    ap.add_argument("--classify", metavar="PATH", help="只打印某文件的分类判定")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()

    # ★ 先加载仓库声明（.guardrc.json）——必须在任何判定之前，
    #   否则 --classify 等入口会绕过 declared_public（自测踩坑）。
    global _DECLARED_PUBLIC, _EXTRA_DENY, _EXTRA_ALLOW
    _DECLARED_PUBLIC, _EXTRA_DENY, _EXTRA_ALLOW = load_guardrc()

    if a.classify:
        cls, verdict, why, scan = classify(a.classify)
        print(f"{a.classify}\n  类别: {cls}\n  判定: {verdict}\n  说明: {why}\n  内容扫描: {scan}")
        return 0 if verdict != "deny" else 1

    mode = ("staged" if not (a.all or a.worktree or a.refs or a.history)
            else "tree" if a.all else "worktree" if a.worktree
            else "refs" if a.refs else "history")

    real_names = load_real_names()
    violations = []
    seen = set()
    for path, text in MODES[mode]():
        for v in evaluate(mode, path, text, real_names):
            key = (v.path, v.reason, v.fragment)
            if key in seen:
                continue
            seen.add(key)
            violations.append(v)

    if a.json:
        print(json.dumps({"mode": mode, "violations": [
            {"layer": v.layer, "path": v.path, "reason": v.reason, "fragment": v.fragment}
            for v in violations]}, ensure_ascii=False, indent=1))
        return 1 if violations else 0

    if not violations:
        names_note = f"{len(real_names)} 个敏感词" if real_names else "无词表"
        print(f"✅ public_repo_guard[{mode}]: 通过（{names_note}）")
        return 0

    print("=" * 68)
    print(f"❌ BLOCKED — public_repo_guard[{mode}] 拦下了 {len(violations)} 处")
    print("=" * 68)
    by_layer = {}
    for v in violations:
        by_layer.setdefault(v.layer, []).append(v)
    if by_layer.get("L1"):
        print("\n【L1 文件分类门】路径本身违规（数据类别错误，与内容无关）：")
        for v in by_layer["L1"][:20]:
            print(v)
    if by_layer.get("L2"):
        print("\n【L2 内容门】检测到 PII / 密钥：")
        for v in by_layer["L2"][:30]:
            print(v)
    if len(violations) > 30:
        print(f"  … 另有 {len(violations) - 30} 处")
    print("""
处理建议：
  · L1 运行数据/备份/日志/导出/凭据 → 移到仓库外（如 ~/var/<项目>/），
    并从索引移除：git rm --cached <文件>（中文名用 glob，别直接管道 git ls-files）
  · L2 PII → 改为合成数据（13800000000 / TEST_USER_001 / user@example.com）
  · L2 密钥 → 放 /etc/<项目>.env 或 ~/.<tool>.env（chmod 600），仓库只留 .env.example
  · L2 绝对路径 → 用 os.path.expanduser("~") / Path(__file__) 动态求值
  · 方案文件（plist/service）→ 改 __HOME__ 占位模板 + 渲染安装脚本
  · 确属误报 → 把该值写成合成形式或加入 ALLOW_CONTENT，**不要**用 PII_GUARD_ALLOW 绕过
""")
    return 1


if __name__ == "__main__":
    sys.exit(main())

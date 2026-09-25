#!/usr/bin/env python3
"""PII 闸门负向测试 —— 证明「违规输入必然变红」，而不只是「干净仓库是绿的」。

落位：仓库 tests/ 目录（按 PR 流程进仓）。不在仓库里也能独立跑：
    PII_GUARD_ENGINE=<引擎路径> python3 test_pii_gate_negative.py

设计要点（每条都是踩过的坑，改动前先读 references/gate-negative-testing.md §6）：
  1. 夹具仓是临时目录，绝不碰真实仓库；词表放在夹具仓**之外**——放仓内会被
     引擎当仓库内容扫到而自我触发。
  2. **夹具的 git 调用必须走 _git()**：净化 GIT_DIR / GIT_WORK_TREE 等变量。
     闸门的 pre-commit 钩子跑测试套件时，git 会把这些变量导出给子进程；夹具若
     不净化，`git init/add/commit` 会打到外层真实仓库上（见 test_7）。
  3. canary 在源码里只用码点构造，保证本测试文件自身不含明文，否则闸门会把
     测试文件自己判红（自指）。
  4. 断言必须同时锚定「退出码」与「finding 的归属」：只断言非 0 会被无关噪音
     （如 digest 撞手机号正则）伪装成通过。
  5. 4c 记录的是当前引擎的真实语义：删掉声明就是 fail-open，因此这条以
     「声明必须存在」的形式由本测试守住，而不是靠引擎兜底。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

# SECURITY FIXTURE —— 合成姓名，非真人。仅在本测试内构造/使用，绝不进任何词表。
CANARY = "".join(chr(c) for c in (0x90D7, 0x783A, 0x94EE))
TEST_KEY = "negtest-key-not-a-secret"
CLEAN_BODY = "hello fixture\nline two\n"

PHONE_RE = re.compile(r"1[3-9]\d{9}(?!\d)")

# 这些变量会把子进程里的 git 指向**外层仓库**：闸门的 pre-commit 钩子跑测试套件
# 时 git 会导出它们，夹具若不净化，夹具内容就会被提交进真实仓库。
_GIT_ENV_DROP = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX",
    "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES",
)


def git_env() -> dict:
    """给夹具用的干净环境：清掉所有会把 git 指向外层仓库的变量。"""
    e = dict(os.environ)
    for k in _GIT_ENV_DROP:
        e.pop(k, None)
    return e


@contextmanager
def clean_git_env():
    """临时把 _GIT_ENV_DROP 从 os.environ 里摘掉。

    引擎内部自己会 shell out 到 git（ls-files / rev-list 等），它继承 os.environ，
    所以在恶意环境下跑引擎会去扫**外层仓库**、得出「零 finding」的假结果 ——
    实测正是这两个用例失败。夹具运行引擎时必须一并净化。
    """
    saved = {k: os.environ[k] for k in _GIT_ENV_DROP if k in os.environ}
    for k in saved:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        os.environ.update(saved)


def _load_engine():
    cand = os.environ.get("PII_GUARD_ENGINE")
    if not cand:
        cand = str(Path(__file__).resolve().parents[1] / "scripts" / "public_repo_guard.py")
    p = Path(cand)
    if not p.exists():
        raise unittest.SkipTest(f"找不到引擎（设 PII_GUARD_ENGINE 指定）: {p}")
    spec = importlib.util.spec_from_file_location("_prg_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PRG = _load_engine()


def build_hashfile(entries, key: str) -> str:
    """用被测引擎自己的 HMAC/归一化实现生成 digest 行（不复制第二套正则）。

    只做「滑窗 → normalize → HMAC」这三步；如果引擎的窗口规则漂移，生成的 digest
    会失配，用例 2/3 会立刻失败并暴露漂移。
    """
    hmac_fn = PRG.make_hmac_fn(key.encode())
    windows = set()
    for e in entries:
        t = PRG.normalize_for_pii(e)
        for n in (2, 3):
            for i in range(len(t) - n + 1):
                w = t[i:i + n]
                if all(PRG.is_cjk_or_alnum(c) for c in w):
                    windows.add(w)
    sorted_digests = sorted(hmac_fn(w.encode()) for w in windows)
    # 结构照抄 pii_digest.py（version/window_lengths/fingerprint/digests）
    lines = [
        "# negtest fixture — keyed HMAC only, no plaintext",
        "version: 1",
        "generated_at: 2026-01-01T00:00:00+00:00",
        "window_lengths: 2,3",
        "all_cjk_alnum: true",
        "entry_slots: %d" % (len(sorted_digests) or 1),
        "# --- digests ---",
    ]
    return "\n".join(lines + sorted_digests) + "\n"


class PiiGateNegativeTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="piigate-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "repo"
        self.outside = Path(tmp.name) / "outside"          # 词表放仓外
        self.root.mkdir()
        self.outside.mkdir()
        (self.root / "scripts").mkdir()
        (self.root / ".github").mkdir()
        shutil.copy2(PRG.__file__, self.root / "scripts" / "public_repo_guard.py")
        (self.root / ".guardrc.json").write_text(json.dumps({
            "pii_gate": {"require_denylist": True, "require_hash_file": True,
                         "require_key": True, "hash_file": ".github/pii-hashes.txt"}
        }), encoding="utf-8")
        self.denylist = self.outside / "fixture-denylist.txt"
        self.denylist.write_text(CANARY + "\n", encoding="utf-8")

    # ── 夹具工具 ────────────────────────────────────────────────
    def _git(self, *args, cwd=None, check=True):
        """夹具里唯一的 git 入口：强制净化环境，防止打到外层仓库。"""
        return subprocess.run(["git", *args], cwd=str(cwd or self.root),
                              env=git_env(), check=check,
                              capture_output=True, text=True)

    def _assert_fixture_isolated(self):
        """夹具仓必须就是它自己；一旦 git 解析到别处，立即大声失败。"""
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        self.assertEqual(os.path.realpath(top), os.path.realpath(str(self.root)),
                         f"夹具仓不在临时目录里（git 被打到了 {top}）—— 立即停止，"
                         f"否则会污染真实仓库")

    def _write_content(self, name: str, body: str):
        (self.root / name).write_text(body, encoding="utf-8")

    def _write_hashfile(self, key=TEST_KEY, entries=None):
        (self.root / ".github" / "pii-hashes.txt").write_text(
            build_hashfile(entries or [CANARY], key), encoding="utf-8")

    def _commit(self, msg="fixture"):
        self._git("init", "-q", check=False)
        self._assert_fixture_isolated()
        self._git("add", "-A")
        self._git("-c", "user.email=t@example.test", "-c", "user.name=t",
                  "commit", "-qm", msg)

    def _run(self, mode: str, *, key=TEST_KEY, denylist=True):
        env = {PRG.PII_HMAC_KEY_ENV: key} if key else {}
        files = [str(self.denylist)] if denylist else ["/nonexistent/denylist.txt"]
        with clean_git_env(), \
             mock.patch.object(PRG, "DENYLIST_FILES", files), \
             mock.patch.object(PRG, "PII_HMAC_KEY_FILE", "/nonexistent/key"), \
             mock.patch.dict(os.environ, env, clear=False):
            if not key:
                os.environ.pop(PRG.PII_HMAC_KEY_ENV, None)
            eng = PRG.GuardEngine(repo_path=str(self.root))
            eng.evaluate(mode=mode)
        cats = {f.category for f in eng.findings}
        targets = " ".join(f.target for f in eng.findings)
        return eng.findings, cats, targets

    # ── 用例 1：干净夹具必须绿（防误杀）─────────────────────────
    def test_1_clean_fixture_passes(self):
        self._write_content("clean.txt", CLEAN_BODY)
        self._write_hashfile()
        self._commit()
        findings, cats, targets = self._run("head")
        self.assertEqual([], [f"{f.category}:{f.target}" for f in findings],
                         f"干净夹具被判红（误杀会让闸门被关掉）: {cats} {targets}")

    # ── 用例 2：合成 canary 必须红 ───────────────────────────────
    def test_2_synthetic_canary_fails(self):
        self._write_content("offender.txt", CLEAN_BODY + CANARY + "\n")
        self._write_hashfile()
        self._commit()
        findings, cats, targets = self._run("head")
        self.assertIn("PII_HASH_MATCH", cats, f"canary 没被姓名维拦住: {cats} {targets}")
        self.assertIn("offender.txt", targets,
                      f"红了但不是 canary 那条（噪音伪装成通过）: {cats} {targets}")

    # ── 用例 3：先提交、再 .gitignore —— 历史维必须仍拦住 ────────
    def test_3_committed_then_ignored_still_fails_in_history(self):
        self._write_content("offender.txt", CLEAN_BODY + CANARY + "\n")
        self._write_content("clean.txt", CLEAN_BODY)
        self._write_hashfile()
        self._commit("leak")
        # 绕过动作：加 .gitignore 并从索引移除（文件仍在磁盘、内容未改）
        self._write_content(".gitignore", "offender.txt\n")
        self._git("rm", "-q", "--cached", "offender.txt")
        self._git("add", ".gitignore")
        self._git("-c", "user.email=t@example.test", "-c", "user.name=t",
                  "commit", "-qm", "hide")
        findings, cats, targets = self._run("history")
        self.assertIn("PII_HASH_MATCH", cats,
                      f"历史维漏了「先提交再忽略」的绕过: {cats} {targets}")
        self.assertIn("offender.txt", targets,
                      f"历史维红了但不是被藏起来的那条: {cats} {targets}")

    # ── 用例 4：缺依赖必须红（fail-closed）───────────────────────
    def test_4a_missing_hmac_key_fails(self):
        self._write_content("clean.txt", CLEAN_BODY)
        self._write_hashfile()
        self._commit()
        findings, cats, _ = self._run("head", key=None)
        self.assertIn("GATE_CONFIG_MISSING", cats, f"缺密钥没红: {cats}")
        self.assertTrue(any(PRG.PII_HMAC_KEY_ENV in f.target for f in findings),
                        "缺密钥的红灯没指向 PII_HMAC_KEY")

    def test_4b_missing_hashfile_fails(self):
        self._write_content("clean.txt", CLEAN_BODY)
        self._commit()
        findings, cats, _ = self._run("head")
        self.assertIn("GATE_CONFIG_MISSING", cats, f"缺哈希表没红: {cats}")
        self.assertTrue(any("pii-hashes" in f.target for f in findings),
                        "缺哈希表的红灯没指向哈希表文件")

    def test_4c_gate_declaration_must_exist(self):
        """删掉仓库 .guardrc.json 的 pii_gate 声明＝一条让 CI 变绿的一行捷径。

        引擎是分发的，不能对全机队强制要求声明，所以这条由仓库自己的测试守住：
        声明缺失即失败，删声明无法把闸门改绿。

        注意断言对象是**真实仓库**的 .guardrc.json（本文件所在仓），不是夹具自己
        写的那份 —— 断言夹具等于什么都没证明。
        """
        repo_root = Path(__file__).resolve().parents[1]
        rc_path = repo_root / ".guardrc.json"
        if not rc_path.exists():
            self.skipTest("不在仓库内运行（无 .guardrc.json）—— 本用例只在仓内生效")
        rc = json.loads(rc_path.read_text(encoding="utf-8"))
        gate = rc.get("pii_gate") or {}
        self.assertTrue(gate, "仓库必须声明 pii_gate（否则缺输入会静默 PASS）")
        for k in ("require_denylist", "require_hash_file", "require_key", "hash_file"):
            self.assertIn(k, gate, f"pii_gate 声明缺 {k}")

    # ── 自检 5：本文件与引擎源码不得含 canary 明文（否则闸门扫到自己是红）──
    def test_5_self_source_has_no_plaintext_canary(self):
        src = Path(__file__).read_text(encoding="utf-8")
        self.assertNotIn(CANARY, src, "本测试源码含 canary 明文 → 闸门会判红本文件")
        self.assertNotIn(CANARY, (self.root / "scripts" / "public_repo_guard.py")
                         .read_text(encoding="utf-8"), "引擎源码含 canary 明文")

    # ── 自检 6：夹具的 digest 文件不得被内建正则误伤 ─────────────
    def test_6_fixture_digest_file_is_regex_clean(self):
        """digest 是随机十六进制，可能含 1[3-9]\\d{9} 这类 11 位数字串而撞手机号规则。

        实测：pii_digest.py 的 DECOY 填充位里就有这样一段，导致哈希表自身被判
        「手机号码」。夹具必须先证明自己是干净的，否则用例 1 的红来自噪音。
        """
        text = build_hashfile([CANARY], TEST_KEY)
        m = PHONE_RE.search(text)
        self.assertIsNone(m, f"夹具 digest 文件撞手机号正则: {m.group(0) if m else ''}"
                             " → 用例 1 会被噪音判红，请换 key 或豁免该路径")

    # ── 自检 7：夹具的 git 必须打在自己身上，不能打到外层仓库 ────
    def test_7_fixture_git_is_isolated_from_outer_repo(self):
        """复现真实事故：闸门的 pre-commit 钩子跑测试套件时，git 会导出 GIT_DIR 与
        GIT_WORK_TREE，指向**外层真实仓库**。夹具若不净化这两个变量，它的
        `git init/add/commit` 就会把夹具内容提交进真实仓库 —— 实测发生过：夹具把
        clean.txt 之类文件提交到了外层分支，并连带把整套测试打红（2F+4E）。
        """
        outer = Path(tempfile.mkdtemp(prefix="outer-"))
        self.addCleanup(shutil.rmtree, str(outer), True)
        subprocess.run(["git", "init", "-q"], cwd=outer, env=git_env(), check=True)
        (outer / "real.txt").write_text("must-not-change\n", encoding="utf-8")
        for args in (["add", "-A"], ["commit", "-qm", "outer baseline"]):
            subprocess.run(["git", "-c", "user.email=o@example.test",
                            "-c", "user.name=o", *args],
                           cwd=outer, env=git_env(), check=True)
        head_before = self._git("rev-parse", "HEAD", cwd=outer).stdout.strip()
        status_before = self._git("status", "--porcelain", cwd=outer).stdout

        # 恶意环境：把 git 指向外层仓库，再正常跑夹具
        with mock.patch.dict(os.environ, {"GIT_DIR": str(outer / ".git"),
                                          "GIT_WORK_TREE": str(outer)}, clear=False):
            self._write_content("clean.txt", CLEAN_BODY)
            self._write_hashfile()
            self._commit()                      # 必须仍然提交进夹具仓

        self.assertEqual(self._git("rev-parse", "HEAD", cwd=outer).stdout.strip(),
                         head_before, "★ 外层仓库被夹具提交污染了（GIT_DIR 未净化）")
        self.assertEqual(self._git("status", "--porcelain", cwd=outer).stdout,
                         status_before, "外层仓库的工作区被夹具改动了")


if __name__ == "__main__":
    unittest.main(verbosity=2)

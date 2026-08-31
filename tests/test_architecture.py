#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""架构不变量测试（2026-08-31 定稿）—— 锁死三层职责不串线。

核心命题（用户定稿，勿回退）：
  1. ALLOW/REJECT 只能来自 L2 job_decision.Decision.action；
  2. L3 value_score 只产出分数/档位，永不产出 REJECT 语义；
  3. boss_apply 主流程中，L3 结果不参与任何「投不投」布尔判决；
  4. 权重版本化：VSCORE_VERSION 存在，基准岗位集存在且非空。

防止的退化：value_score 偷偷变成第二个 job_decision（
「58分要不要直接不投」「65分要不要设成硬线」的老路）。
"""
import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import job_decision  # noqa: E402
import value_score  # noqa: E402


class TestL2L3Separation(unittest.TestCase):
    """L2 有 action；L3 没有 action/REJECT 语义。"""

    def test_decision_has_action_not_score(self):
        fields = {f.name for f in job_decision.Decision.__dataclass_fields__.values()}
        self.assertIn("action", fields)
        self.assertNotIn("score", fields)     # L2 不排序
        self.assertNotIn("tier", fields)

    def test_valuescore_has_score_not_action(self):
        fields = {f.name for f in value_score.ValueScore.__dataclass_fields__.values()}
        self.assertIn("score", fields)
        self.assertIn("tier", fields)
        self.assertNotIn("action", fields)    # L3 不裁决
        self.assertNotIn("decision", fields)

    def test_l3_module_has_no_reject_constant(self):
        # 检查代码级（AST 字符串字面量，跳过 docstring/注释位置）。
        # 防的是 `if vs.score < 60: return "REJECT"` 这类代码，不是文档。
        tree = ast.parse((ROOT / "value_score.py").read_text())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        code_rejects = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "REJECT" in node.value:
                parent = parents.get(node)
                if not isinstance(parent, ast.Expr):   # Expr = docstring/注释串，不算
                    code_rejects += 1
        self.assertEqual(code_rejects, 0, "value_score.py 代码中出现 REJECT 字样 = 架构违规")

    def test_l3_never_returns_reject(self):
        # 任意输入（含最低薪+最差条件）也只返回分数，不返回 REJECT 语义
        vs = value_score.value_score("某公司", "客服", "纯客服", "2-3K",
                                     city="北京", salary_band="<5K")
        self.assertIsInstance(vs, value_score.ValueScore)
        self.assertIn(vs.tier, ("HIGH", "NORMAL", "LOW"))  # 永远是排序档位


class TestMainFlowSeparation(unittest.TestCase):
    """boss_apply 主流程：L3 结果不参与投不投判决。"""

    def _main_source(self) -> str:
        return (ROOT / "boss_apply.py").read_text()

    def _judgement_sites(self) -> list:
        """从主流程 AST 找 L3 变量 (vs.) 出现的所有属性访问。"""
        tree = ast.parse(self._main_source())
        sites = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id in ("vs", "vs_val"):
                    sites.append(node.attr)
        return sites

    def test_l3_attribute_access_is_ranking_only(self):
        # vs.score / vs.tier 用于展示和 reason 追加是允许的；
        # 但 anywhere 出现 vs.action 或 vs 进入条件判断 = 架构违规。
        tree = ast.parse(self._main_source())
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for side in [node.left] + list(node.comparators):
                    if isinstance(side, ast.Attribute):
                        if isinstance(side.value, ast.Name) and side.value.id in ("vs", "vs_val"):
                            violations.append(f"L3 进入比较: {ast.unparse(node)}")
        self.assertEqual(violations, [])
        # 展示用途的访问允许，但要证明它确实存在（否则说明 L3 压根没接上）
        sites = self._judgement_sites()
        self.assertIn("score", sites)
        self.assertIn("tier", sites)

    def test_reject_only_from_l2(self):
        src = self._main_source()
        # 主流程里 REJECT 判决只允许来自 jd.action
        self.assertIn('jd.action == "REJECT"', src)
        # L3 调用点附近不得有 return "skip"
        i = src.find("value_score(")
        j = src.find("print(f\"  [💎]", i)
        self.assertGreater(j, i)
        between = src[i:j]
        self.assertNotIn('return "skip"', between)
        self.assertNotIn("REJECT", between)


class TestVersioning(unittest.TestCase):
    """权重版本化机制必须存在。"""

    def test_version_constant(self):
        self.assertTrue(hasattr(value_score, "VSCORE_VERSION"))
        self.assertRegex(value_score.VSCORE_VERSION, r"^\d+\.\d+$")

    def test_benchmark_set_exists(self):
        p = ROOT / "tests" / "benchmark_roles.json"
        self.assertTrue(p.exists())
        data = json.loads(p.read_text())
        self.assertGreaterEqual(len(data["roles"]), 8)
        self.assertIn("baseline_version", data)
        self.assertIn(f"v{data['baseline_version']}_scores", data)

    def test_weights_sum_100(self):
        total = sum(value_score.WEIGHTS.values())
        self.assertEqual(total, 100)

    def test_all_cases_reproducible(self):
        # 基准集可复现（防未来改权重把锚点岗位打乱而不自知）
        import subprocess
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "vscore_benchmark.py"), "--json"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["version"], value_score.VSCORE_VERSION)
        baseline = out["baseline"]
        for row in out["results"]:
            b = baseline.get(row["id"])
            if b is not None:
                self.assertEqual(b, row["score"],
                                 f"{row['id']} 相对基线 v1.0 漂移: {b}→{row['score']}")


if __name__ == "__main__":
    unittest.main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VSCORE 权重演进的基准对比工具（2026-08-31 定稿）。

每次修改 value_score.py 的 WEIGHTS / 维度逻辑后：
    PYTHONPATH="" /usr/bin/python3 scripts/vscore_benchmark.py
输出当前版本 vs v1.0 基线对照表，逐岗位标注变化。
变化必须人话可解释；解释不了的改动 → 回退。

输出两种模式：
    默认     当前版本表 + 与基线 diff
    --json   机器可读（CI/对比用）
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_decision import evaluate_job  # noqa: E402
from match_engine import explain_match, load_candidate_profile  # noqa: E402
from value_score import VSCORE_VERSION, value_score  # noqa: E402

BENCH = ROOT / "tests" / "benchmark_roles.json"
_CONFIG = {}
if (ROOT / "config.json").exists():
    _CONFIG = json.loads((ROOT / "config.json").read_text())


def run_all() -> dict:
    data = json.loads(BENCH.read_text())
    profile = load_candidate_profile(_CONFIG)
    out = {"version": VSCORE_VERSION, "baseline": data.get("v1.0_scores", {}),
           "results": []}
    for r in data["roles"]:
        dec = evaluate_job(r["company"], r["title"], r["desc"], r["salary"],
                           city=r["city"], cfg=_CONFIG)
        if dec.action == "REJECT":
            out["results"].append({"id": r["id"], "note": r.get("note", ""),
                                   "action": "REJECT", "reason": dec.reason})
            continue
        mr = explain_match(r["title"], r["desc"], company=r["company"],
                           salary=r["salary"], city=r["city"], cfg=_CONFIG)
        vs = value_score(r["company"], r["title"], r["desc"], r["salary"],
                         city=r["city"], decision=dec, match_result=mr,
                         salary_band=dec.salary_band)
        base = data.get("v1.0_scores", {}).get(r["id"])
        out["results"].append({
            "id": r["id"], "note": r.get("note", ""),
            "action": dec.action, "priority": dec.priority,
            "score": vs.score, "tier": vs.tier,
            "baseline": base,
            "delta": (vs.score - base) if base is not None else None,
        })
    return out


def fmt(rows: list) -> str:
    lines = [f"{'岗位ID':<26}{'分':>5} {'档':<7}{'基线':>5} {'Δ':>5}  说明"]
    for r in rows:
        if r["action"] == "REJECT":
            lines.append(f"{r['id']:<26}{'REJ':>5} {'—':<7}{'—':>5} {'—':>5}  {r['note']} ({r['reason'][:24]})")
        else:
            d = f"{r['delta']:+d}" if r["delta"] is not None else "  ?"
            lines.append(f"{r['id']:<26}{r['score']:>5} {r['tier']:<7}{str(r['baseline'] if r['baseline'] is not None else '?'):>5} {d:>5}  {r['note']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = run_all()
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    ver = out["version"]
    print(f"=== VSCORE v{ver} 基准岗位结果 ===")
    print(fmt(out["results"]))
    deltas = [r["delta"] for r in out["results"] if r.get("delta") is not None and r["delta"] != 0]
    if out["version"] == "1.0":
        print(f"\n(v1.0 为基线版本，首次记录)")
    elif deltas:
        print(f"\n⚠️ 相对 v1.0 有 {len(deltas)} 个岗位得分变化: {deltas}")
        print("   请人话解释变化原因；解释不了的改动 → 回退")
    else:
        print(f"\n✓ 相对 v1.0 无变化")


if __name__ == "__main__":
    main()
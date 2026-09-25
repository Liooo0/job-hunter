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

2026-09-25：加 --defaults / JOBHUNTER_CONFIG —— 基线的可比性依赖于「用哪份配置」，
原先默认吃本机 config.json（.gitignore 的个人文件），导致同一份基线在任何 clone
上都对不上。测试/CI 用 --defaults 走仓内默认配置，结果与机器无关。
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_decision import evaluate_job  # noqa: E402
from match_engine import explain_match, load_candidate_profile  # noqa: E402
from value_score import VSCORE_VERSION, value_score  # noqa: E402

BENCH = ROOT / "tests" / "benchmark_roles.json"
_CONFIG_PATH = os.environ.get("JOBHUNTER_CONFIG")


def _resolve_config(defaults: bool, config_path=None):
    """决定本次跑用哪份配置，返回 (cfg, 说明)。

    defaults=True  → 只用**仓内默认配置**（shared 的 FALLBACK），忽略本机
                     config.json。CI/测试走这条：任何人都能得到同一组分数。
    config_path / JOBHUNTER_CONFIG → 显式指定一份配置（可复现的外部输入）。
    否则            → 沿用本机 config.json（作者日常用法，与基线不可比）。

    2026-09-25 加入这个开关的原因：基线里的分数是**在作者的 config.json 下**
    记录的，而那份文件被 .gitignore（含本机路径等个人信息），任何 clone 都拿不到
    → 同一份基线在不同机器上注定对不上（实测：本机漂移 0 项；干净检出下
    ai_12k 82→83、ai_8k 73→77、ai_startup_10k 80→81 三项「漂移」，逐项核对后
    全是配置来源不同造成的假漂移，不是评分规则变了）。
    """
    import shared
    if defaults:
        return shared.load_config(skill_dir=Path(tempfile.mkdtemp())), "仓内默认配置（FALLBACK）"
    path = config_path or _CONFIG_PATH
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8")), f"外部配置 {path}"
    if (ROOT / "config.json").exists():
        return (json.loads((ROOT / "config.json").read_text(encoding="utf-8")),
                "本机 config.json（与基线不可比）")
    return {}, "空配置（无 config.json）"


def run_all(cfg=None, defaults: bool = False) -> dict:
    if cfg is None:
        cfg, _ = _resolve_config(defaults)
    data = json.loads(BENCH.read_text())
    bkey = f"v{data.get('baseline_version', '1.0')}_scores"
    profile = load_candidate_profile(cfg)
    out = {"version": VSCORE_VERSION, "baseline": data.get(bkey, {}),
           "baseline_key": bkey, "results": []}
    for r in data["roles"]:
        dec = evaluate_job(r["company"], r["title"], r["desc"], r["salary"],
                           city=r["city"], cfg=cfg)
        if dec.action == "REJECT":
            out["results"].append({"id": r["id"], "note": r.get("note", ""),
                                   "action": "REJECT", "reason": dec.reason})
            continue
        mr = explain_match(r["title"], r["desc"], company=r["company"],
                           salary=r["salary"], city=r["city"], cfg=cfg)
        vs = value_score(r["company"], r["title"], r["desc"], r["salary"],
                         city=r["city"], decision=dec, match_result=mr,
                         salary_band=dec.salary_band)
        base = data.get(bkey, {}).get(r["id"])
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
    ap.add_argument("--defaults", action="store_true",
                    help="只用仓内默认配置（忽略本机 config.json）—— CI/测试用，与机器无关")
    ap.add_argument("--config", help="显式指定一份配置（等价于 JOBHUNTER_CONFIG）")
    args = ap.parse_args()
    cfg, src = _resolve_config(args.defaults, args.config)
    out = run_all(cfg=cfg)
    if args.json:
        out["config_source"] = src
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    ver = out["version"]
    print(f"=== VSCORE v{ver} 基准岗位结果 ===")
    print(f"配置来源: {src}")
    if "config.json" in src or src.startswith(("外部配置", "空配置")):
        print("⚠️  非仓内默认配置 → 分数与仓内基线不可比，Δ 不代表回归")
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
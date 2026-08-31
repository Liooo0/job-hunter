#!/usr/bin/env python3
"""从当前 VSCORE 运行态生成 v1.1 基线表（人工审查后写入 benchmark_roles.json）。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
out = json.loads(subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "vscore_benchmark.py"), "--json"],
    capture_output=True, text=True, cwd=ROOT, check=True).stdout)
table = {}
for r in out["results"]:
    if r["action"] == "REJECT":
        table[r["id"]] = None
    else:
        table[r["id"]] = {"score": r["score"], "tier": r["tier"]}
print(json.dumps(table, ensure_ascii=False, indent=2))

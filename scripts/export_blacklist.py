#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出 job_decision 中的公司黑名单与外包名单至 datasets/public/blacklist.json。

仅导出公司名称与公共可解释分类理由，不包含任何个人敏感信息。
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import job_decision
except ImportError as e:
    print(f"Error importing job_decision: {e}", file=sys.stderr)
    sys.exit(1)

OUTPUT_FILE = PROJECT_ROOT / "datasets" / "public" / "blacklist.json"

# 公共可解释原因映射表
PUBLIC_REASONS = {
    "法本": ("blacklist", "IT外包服务类企业"),
    "珍岛": ("blacklist", "销售型SaaS类企业"),
    "外企德科": ("outsourcing", "人力资源服务与外包企业"),
    "中软国际": ("outsourcing", "IT外包服务企业"),
    "软通动力": ("outsourcing", "IT外包服务企业"),
    "博彦科技": ("outsourcing", "IT外包服务企业"),
    "文思海辉": ("outsourcing", "IT外包服务企业"),
    "中电金信": ("outsourcing", "IT外包服务企业"),
    "人瑞人才": ("outsourcing", "人力资源服务与外包企业"),
    "探迹": ("outsourcing", "销售型SaaS类企业"),
}


def get_git_version() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=True
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def export_blacklist():
    blacklist_names = getattr(job_decision, "COMPANY_BLACKLIST", [])
    outsourcing_names = getattr(job_decision, "OUTSOURCING_COMPANIES", [])

    git_ver = get_git_version()
    now_iso = datetime.now().isoformat(timespec="seconds")

    records = []
    # 1. 黑名单
    for name in blacklist_names:
        item_type, reason = PUBLIC_REASONS.get(name, ("blacklist", "黑名单企业"))
        records.append({
            "name": name,
            "type": item_type,
            "reason": reason,
            "exported_at": now_iso,
            "source_version": git_ver
        })

    # 2. 外包名单
    for name in outsourcing_names:
        item_type, reason = PUBLIC_REASONS.get(name, ("outsourcing", "外包/人服类企业"))
        records.append({
            "name": name,
            "type": item_type,
            "reason": reason,
            "exported_at": now_iso,
            "source_version": git_ver
        })

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✅ 成功导出 {len(records)} 条公司黑名单/外包规则至 {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")
    return len(records)


if __name__ == "__main__":
    count = export_blacklist()
    sys.exit(0 if count == 10 else 1)

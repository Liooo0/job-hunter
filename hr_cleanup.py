#!/usr/bin/env python3
"""
HR 消息清扫 — 只对有意向的回复，拒绝/系统消息自动归档

用法:
    python3 hr_cleanup.py            # 清扫 pending_replies：拒绝归档，保留有意向的
    python3 hr_cleanup.py --list     # 只列出分类结果，不归档
"""
import json
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).parent
PENDING_DIR = SKILL_DIR / "pending_replies"
ARCHIVE_DIR = SKILL_DIR / "archived_replies"

# 明确拒绝 / 无意义消息（自动归档，不回复）
REJECT_KW = [
    "不合适", "不太匹配", "不匹配", "招够了", "不需要", "暂时不", "已招到",
    "岗位已关闭", "简历不匹配", "很抱歉", "祝您找到", "祝您早日", "已结束",
    "停止招聘", "暂不推进", "综合评估", "感谢关注", "感谢同学", "招满", "满了",
    "背景不同", "资质", "27届", "26届", "25届", "面向", "不打算", "不太合适",
    "不满足", "要求不匹配", "不吻合",
]
# 系统消息（自动归档）
SYS_KW = ["您正在与Boss", "您的附件简历", "已读", "对方已读"]


def classify(msg: str) -> tuple[str, str]:
    """返回 (kind, reason)。kind: reject(明确拒绝) / system(系统) / interest(有意向)
    改进(2026-08-12): 返回命中关键词作为分类依据，避免误归档时无法追溯。"""
    for k in SYS_KW:
        if k in msg:
            return "system", f"系统关键词[{k}]"
    for k in REJECT_KW:
        if k in msg:
            return "reject", f"拒绝关键词[{k}]"
    return "interest", "无拒绝/系统信号"


def main():
    clean = "--list" not in sys.argv
    ARCHIVE_DIR.mkdir(exist_ok=True)

    stats = {"reject": 0, "system": 0, "interest": 0}
    interesting = []

    for f in sorted(PENDING_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        msg = d.get("message", "")
        kind, basis = classify(msg)
        stats[kind] += 1

        if kind == "interest":
            interesting.append(d)
            print(f"💬 [有意向] {d.get('company','')} | {d.get('name','')}")
            print(f"    HR说: {msg[:100]}")
            print(f"    岗位: {d.get('job_context',{}).get('job_title','未知')}")
            print()
        else:
            label = "拒绝" if kind == "reject" else "系统消息"
            print(f"⏭️  [{label}] {d.get('company','')} | {d.get('message','')[:50]}")
            print(f"    分类依据: {basis}")
            if clean:
                # 归档：移到 archived，不删除；保留原消息+分类结果+依据+时间
                d["status"] = "archived"
                d["archived_at"] = datetime.now().isoformat()
                d["classify_basis"] = basis
                dest = ARCHIVE_DIR / f.name
                dest.write_text(json.dumps(d, ensure_ascii=False, indent=2))
                if dest.exists():
                    f.unlink()  # 先写后删，确保归档成功才移除原文件

    print()
    print("=" * 50)
    print(f"📊 分类结果: 拒绝 {stats['reject']} | 系统 {stats['system']} | 有意向 {stats['interest']}")
    if clean:
        print(f"   拒绝/系统消息已归档 → archived_replies/（不回复，不浪费）")
        print(f"   保留有意向 → pending_replies/（等待自动回复）")
    if interesting:
        print()
        print("✅ 有意向的消息已保留，随时可以回复")
    else:
        print("\nℹ️  当前没有需要回复的有意向消息")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
投递跟进提醒 — 找出投了 N 天还没回复的岗位，生成跟进清单

用法:
    python3 follow_up.py                # 默认 3 天
    python3 follow_up.py --days 5       # 自定义天数
    python3 follow_up.py --mark 公司名 岗位名 已回复   # 标记跟进结果
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).parent
FOLLOWUP_FILE = SKILL_DIR / "followups.json"


def load_followups() -> dict:
    """已跟进记录: {公司|岗位: {"date": ..., "status": ...}}"""
    if FOLLOWUP_FILE.exists():
        return json.loads(FOLLOWUP_FILE.read_text(encoding="utf-8"))
    return {}


def save_followups(data: dict):
    FOLLOWUP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def collect_applied() -> list[dict]:
    """收集所有城市的投递记录"""
    items = []
    for f in sorted(SKILL_DIR.glob("boss-*-log.json")):
        city = f.stem.replace("boss-", "").replace("-log", "")
        try:
            log = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for e in log.get("applied", []):
            items.append({
                "company": e.get("company", ""),
                "job": e.get("job", ""),
                "salary": e.get("salary", ""),
                "score": e.get("score", 0),
                "city": city,
                "keyword": e.get("keyword", ""),
                "time": e.get("time", ""),  # 旧数据可能没有
            })
    return items


def parse_time(t: str):
    """解析投递时间，失败返回 None"""
    if not t:
        return None
    try:
        return datetime.fromisoformat(t)
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(description="投递跟进提醒")
    p.add_argument("--days", type=int, default=3, help="超过几天未跟进（默认3）")
    p.add_argument("--max-days", type=int, default=14, help="超过多少天不再跟进（默认14，已凉）")
    p.add_argument("--min-score", type=int, default=40, help="只看评分≥此值的（默认40，匹配度好的才值得跟进）")
    p.add_argument("--mark", nargs="+", help='标记跟进结果: --mark "公司名" "岗位名" 已回复')
    p.add_argument("--unmark-all", action="store_true", help="清空所有跟进标记（重跑用）")
    args = p.parse_args()

    followups = load_followups()

    # ── 标记结果 ──
    if args.mark:
        if len(args.mark) >= 2:
            company, job = args.mark[0], args.mark[1]
            status = " ".join(args.mark[2:]) if len(args.mark) > 2 else "已跟进"
            key = f"{company}|{job}"
            followups[key] = {
                "date": datetime.now().isoformat(),
                "status": status,
            }
            save_followups(followups)
            print(f"✅ 已标记: {company} | {job} → {status}")
        else:
            print("用法: --mark '公司名' '岗位名' 结果")
        return

    if args.unmark_all:
        FOLLOWUP_FILE.unlink(missing_ok=True)
        print("🧹 已清空跟进标记")
        return

    # ── 生成待跟进清单 ──
    now = datetime.now()
    cutoff = now - timedelta(days=args.days)
    stale_cutoff = now - timedelta(days=args.max_days)  # 超过这天=已凉，不追
    items = collect_applied()

    due = []          # 3~14天，黄金窗口
    no_time = []      # 旧数据无日期，建议跟进
    followed = []     # 已标记过

    for it in items:
        key = f"{it['company']}|{it['job']}"
        if key in followups:
            followed.append(it)
            continue
        t = parse_time(it["time"])
        if t is None:
            no_time.append(it)
        elif t < cutoff and t >= stale_cutoff and it["score"] >= args.min_score:
            it["_days"] = (now - t).days
            due.append(it)

    due.sort(key=lambda x: x["_days"], reverse=True)

    # ── 终端输出 ──
    print("=" * 60)
    print(f"🤖 投递跟进提醒  |  {args.days}~{args.max_days}天黄金窗口: {len(due)} 个")
    print(f"   无日期旧数据: {len(no_time)} 个  |  已跟进: {len(followed)} 个")
    print("=" * 60)

    if due:
        print(f"\n🔥 需要跟进 ({len(due)}):")
        for it in due:
            print(f"  [{it['_days']:2d}天] {it['company'][:12]:14s} | {it['job'][:28]:30s} | {it['salary']} | {it['city']}")
        print(f"\n  标记: python3 follow_up.py --mark '公司名' '岗位名' 已回复")

    if no_time:
        print(f"\n⚠️ 无日期旧数据 ({len(no_time)}):")
        for it in no_time[:15]:
            print(f"  {it['company'][:12]:14s} | {it['job'][:28]:30s} | {it['city']}")
        if len(no_time) > 15:
            print(f"  ... 共 {len(no_time)} 个")

    if not due and not no_time:
        print("\n🎉 今天没有需要跟进的岗位")

    # ── HTML 报告 ──
    if due or no_time:
        report_dir = SKILL_DIR / "data" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        fname = report_dir / f"followup_{now.strftime('%Y-%m-%d_%H%M')}.html"

        rows = ""
        for it in due:
            rows += f"""
            <tr>
              <td>{it['_days']}天</td>
              <td>{it['company']}</td>
              <td>{it['job']}</td>
              <td>{it['salary']}</td>
              <td>{it['city']}</td>
              <td>{it['time'][:10] if it['time'] else '?'}</td>
            </tr>"""
        for it in no_time[:15]:
            rows += f"""
            <tr>
              <td>?</td>
              <td>{it['company']}</td>
              <td>{it['job']}</td>
              <td>{it['salary']}</td>
              <td>{it['city']}</td>
              <td>旧数据</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>投递跟进清单</title>
<style>
body {{ font-family: -apple-system, sans-serif; padding: 24px; background: #fafafa; }}
h1 {{ font-size: 22px; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; }}
th, td {{ padding: 8px 12px; border: 1px solid #eee; text-align: left; font-size: 14px; }}
th {{ background: #f0f0f0; }}
.days {{ color: #d33; font-weight: bold; }}
</style></head><body>
<h1>🔥 投递跟进清单 ({len(due)} 个待跟进)</h1>
<p>生成时间: {now.strftime('%Y-%m-%d %H:%M')} | 规则: 投递超过 {args.days} 天未回复</p>
<table>
<tr><th>天数</th><th>公司</th><th>岗位</th><th>薪资</th><th>城市</th><th>投递日期</th></tr>
{rows}
</table>
</body></html>"""
        fname.write_text(html, encoding="utf-8")
        print(f"\n📄 报告: {fname}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""报告生成器 — 终端汇总 + 自包含 HTML 报告

渲染方式（B4 加固）：页面骨架用 string.Template（$占位符），
三个数据区块（投递表/趋势/跳过抽样）由分段函数拼装后整体注入。
所有动态文本字段统一过 _esc() → html.escape，公司名/岗位名含 <>&" 不会破坏页面。
"""

import json
from html import escape
from string import Template
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from shared import load_config, merge_logs, recent_activity, ensure_dir, format_salary

SKILL_DIR = Path(__file__).parent

# 无数据时的占位块（原 {% else %} 分支内容）
NO_DATA_HTML = '<div class="no-data">暂无投递记录</div>'

APPLIED_SECTION_TEMPLATE = Template("""<h2>📋 所有投递记录</h2>
<table>
<thead><tr><th>评分</th><th>公司</th><th>岗位</th><th>薪资</th><th>日志来源</th></tr></thead>
<tbody>
$rows</tbody>
</table>""")

APPLIED_ROW_TEMPLATE = Template(
    '<tr>\n'
    '  <td><span class="score-badge $score_class">$score</span></td>\n'
    '  <td>$company</td>\n'
    '  <td>$job</td>\n'
    '  <td>$salary</td>\n'
    '  <td style="color:#aaa;font-size:12px">$log_file</td>\n'
    '</tr>\n'
)

TREND_SECTION_TEMPLATE = Template("""<h2>📈 近 7 天趋势</h2>
<div class="trend-bar">
$bars</div>""")

TREND_BAR_TEMPLATE = Template(
    '  <div class="trend-day">\n'
    '    <div class="trend-fill" style="height: $height_pct%"></div>\n'
    '    <div class="trend-label">$label</div>\n'
    '  </div>\n'
)

SKIPPED_SECTION_TEMPLATE = Template("""<h2>⏭️ 最近跳过的岗位（抽样）</h2>
<table>
<thead><tr><th>评分</th><th>岗位</th><th>跳过原因</th></tr></thead>
<tbody>
$rows</tbody>
</table>""")

SKIPPED_ROW_TEMPLATE = Template(
    '<tr>\n'
    '  <td><span class="score-badge score-low">$score</span></td>\n'
    '  <td>$job</td>\n'
    '  <td style="color:#888;font-size:13px">$reason</td>\n'
    '</tr>\n'
)

HTML_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Hunter 报告 — $report_time</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f5f5f5; color: #333; padding: 24px; max-width: 960px; margin: 0 auto; }
  h1 { font-size: 24px; margin-bottom: 4px; }
  .subtitle { color: #888; font-size: 14px; margin-bottom: 24px; }
  .cards { display: flex; gap: 16px; margin-bottom: 32px; flex-wrap: wrap; }
  .card { flex: 1; min-width: 140px; background: #fff; border-radius: 10px;
          padding: 20px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
  .card .num { font-size: 40px; font-weight: 700; }
  .card .label { font-size: 13px; color: #999; margin-top: 4px; }
  .card.green .num { color: #22c55e; }
  .card.blue .num { color: #3b82f6; }
  .card.yellow .num { color: #f59e0b; }
  .card.red .num { color: #ef4444; }
  table { width: 100%; border-collapse: collapse; background: #fff;
          border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 32px; }
  th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid #f0f0f0; }
  th { background: #fafafa; font-size: 12px; text-transform: uppercase; color: #888; }
  td { font-size: 14px; }
  .score-badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
                 font-size: 12px; font-weight: 600; }
  .score-high { background: #dcfce7; color: #16a34a; }
  .score-mid { background: #fef3c7; color: #d97706; }
  .score-low { background: #fee2e2; color: #dc2626; }
  .trend-bar { display: flex; gap: 4px; align-items: flex-end; height: 120px; padding: 8px 0; }
  .trend-day { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; }
  .trend-fill { width: 100%; max-width: 40px; background: #3b82f6; border-radius: 4px 4px 0 0;
                min-height: 2px; transition: height .3s; }
  .trend-label { font-size: 11px; color: #999; margin-top: 6px; }
  .no-data { text-align: center; color: #bbb; padding: 40px; }
  h2 { font-size: 18px; margin-bottom: 16px; }
</style>
</head>
<body>
<h1>🤖 Job Hunter 报告</h1>
<div class="subtitle">生成时间: $report_time | 平台: $platform | ⚠️ 未验证投递: $total_uncertain</div>

<div class="cards">
  <div class="card blue">
    <div class="num">$total_jobs_seen</div>
    <div class="label">浏览岗位</div>
  </div>
  <div class="card green">
    <div class="num">$total_applied</div>
    <div class="label">累计投递</div>
  </div>
  <div class="card yellow">
    <div class="num">$total_skipped</div>
    <div class="label">已跳过</div>
  </div>
  <div class="card red">
    <div class="num">$total_failed</div>
    <div class="label">失败</div>
  </div>
</div>

$applied_section

$trend_section

$skipped_section

</body>
</html>""")


def score_class(score: int) -> str:
    if score >= 50:
        return "score-high"
    elif score >= 30:
        return "score-mid"
    return "score-low"


def _esc(value) -> str:
    """动态文本统一 HTML 转义（公司名/岗位名等含 <>&" 时页面不破）。"""
    return escape(str(value)) if value is not None else ""


def _render_applied_section(applied: list) -> str:
    if not applied:
        return NO_DATA_HTML
    rows = "".join(
        APPLIED_ROW_TEMPLATE.substitute(
            score_class=e["score_class"],
            score=e.get("score", 0),
            company=_esc(e.get("company", "")),
            job=_esc(e.get("job", "")),
            salary=_esc(e.get("salary", "")),
            log_file=_esc(e.get("_log_file", "")),
        )
        for e in applied
    )
    return APPLIED_SECTION_TEMPLATE.substitute(rows=rows)


def _render_trend_section(trend_data: list) -> str:
    if not trend_data:
        return ""
    bars = "".join(
        TREND_BAR_TEMPLATE.substitute(
            height_pct=d["height_pct"], label=_esc(d["label"])
        )
        for d in trend_data
    )
    return TREND_SECTION_TEMPLATE.substitute(bars=bars)


def _render_skipped_section(skipped: list) -> str:
    if not skipped:
        return ""
    rows = "".join(
        SKIPPED_ROW_TEMPLATE.substitute(
            score=e.get("score", 0),
            job=_esc(e.get("job", "")),
            reason=_esc(e.get("reason", "")),
        )
        for e in skipped
    )
    return SKIPPED_SECTION_TEMPLATE.substitute(rows=rows)


def generate_html(
    skill_dir: Optional[Path] = None, output_path: Optional[Path] = None
) -> Path:
    """根据所有日志生成自包含 HTML 报告，返回输出路径。"""
    skill_dir = skill_dir or SKILL_DIR
    cfg = load_config(skill_dir)
    merged = merge_logs(skill_dir)
    trend = recent_activity(skill_dir, days=7)

    # 投递记录按评分降序
    applied = sorted(merged["applied"], key=lambda e: e.get("score", 0), reverse=True)
    for e in applied:
        e["score_class"] = score_class(e.get("score", 0))
        salary_raw = e.get("salary", "")
        if salary_raw:
            e["salary"] = format_salary(salary_raw)
        else:
            e["salary"] = ""

    # 跳过记录抽样（最近 30 条）
    skipped = merged["skipped"][-30:]
    for e in skipped:
        e["score"] = e.get("score", 0)

    # 趋势数据 → 适合模板渲染
    max_applied = max((d["applied"] for d in trend), default=1)
    trend_data = []
    for d in trend:
        pct = round(d["applied"] / max_applied * 100) if max_applied > 0 else 0
        trend_data.append({"label": d["date"][5:], "height_pct": max(pct, 5), "count": d["applied"]})

    html = HTML_TEMPLATE.substitute(
        report_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        platform="Boss直聘",
        total_jobs_seen=len(merged["applied"]) + len(merged["skipped"]),
        total_applied=len(merged["applied"]),
        total_skipped=len(merged["skipped"]),
        total_failed=len(merged["failed"]),
        total_uncertain=str(sum(1 for e in merged["applied"] if e.get("status") == "UNCERTAIN")),
        applied_section=_render_applied_section(applied),
        trend_section=_render_trend_section(trend_data),
        skipped_section=_render_skipped_section(skipped),
    )

    # 写入
    if output_path is None:
        report_dir = skill_dir / cfg.get("report_dir", "data/reports")
        ensure_dir(report_dir)
        output_path = report_dir / f"report_{datetime.now().strftime('%Y-%m-%d_%H%M')}.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path


def print_terminal_summary(skill_dir: Optional[Path] = None):
    """在终端打印一次运行的汇总结果。"""
    skill_dir = skill_dir or SKILL_DIR
    cfg = load_config(skill_dir)
    merged = merge_logs(skill_dir)

    applied = merged["applied"]
    skipped = merged["skipped"]
    failed = merged["failed"]

    print(f"""
╔══════════════════════════════════════╗
║  🤖 Job Hunter — {datetime.now().strftime('%Y-%m-%d %H:%M')}  ║
╠══════════════════════════════════════╣
║  平台: Boss直聘                         ║
║  城市: {', '.join(cfg.get('target_cities', [])[:5])}      ║
║  搜索词: {', '.join(cfg.get('search_keywords', [])[:3])}  ║
║  浏览: {len(applied) + len(skipped)} 岗 | 投递: {len(applied)} 岗 | 跳过: {len(skipped)} 岗 ║
╚══════════════════════════════════════╝
""")

    if applied:
        print("  ✅ 所有投递记录（按评分降序）:")
        for e in sorted(applied, key=lambda e: e.get("score", 0), reverse=True):
            salary = format_salary(e.get("salary", ""))
            print(
                f"  [{e.get('score', 0):3d}分] {e.get('company', '?'):15s} | {e.get('job', '')[:25]:25s} | {salary}"
            )

    if failed:
        print(f"\n  ❌ 失败 {len(failed)} 个:")
        for e in failed[-5:]:
            print(f"  - {e.get('job', '')[:30]} → {e.get('error', '')[:60]}")

    uncertain = sum(1 for e in applied if e.get("status") == "UNCERTAIN")
    if uncertain:
        print(f"\n  ⚠️  未验证投递 {uncertain} 个（会话已打开但发送未确认，需人工复核）")


if __name__ == "__main__":
    # 直接运行：生成 HTML 报告 + 终端输出
    cfg = load_config()
    print_terminal_summary()
    path = generate_html()
    print(f"\n📄 HTML 报告已生成: {path}")

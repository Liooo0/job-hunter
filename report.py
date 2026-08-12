#!/usr/bin/env python3
"""报告生成器 — 终端汇总 + 自包含 HTML 报告"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from shared import load_config, merge_logs, recent_activity, ensure_dir, format_salary

SKILL_DIR = Path(__file__).parent

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Hunter 报告 — {{ report_time }}</title>
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
<div class="subtitle">生成时间: {{ report_time }} | 平台: {{ platform }}</div>

<div class="cards">
  <div class="card blue">
    <div class="num">{{ total_jobs_seen }}</div>
    <div class="label">浏览岗位</div>
  </div>
  <div class="card green">
    <div class="num">{{ total_applied }}</div>
    <div class="label">累计投递</div>
  </div>
  <div class="card yellow">
    <div class="num">{{ total_skipped }}</div>
    <div class="label">已跳过</div>
  </div>
  <div class="card red">
    <div class="num">{{ total_failed }}</div>
    <div class="label">失败</div>
  </div>
</div>

{% if new_applied %}
<h2>📋 所有投递记录</h2>
<table>
<thead><tr><th>评分</th><th>公司</th><th>岗位</th><th>薪资</th><th>日志来源</th></tr></thead>
<tbody>
{% for entry in new_applied %}
<tr>
  <td><span class="score-badge {{ entry.score_class }}">{{ entry.score }}</span></td>
  <td>{{ entry.company }}</td>
  <td>{{ entry.job }}</td>
  <td>{{ entry.salary }}</td>
  <td style="color:#aaa;font-size:12px">{{ entry._log_file }}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% else %}
<div class="no-data">暂无投递记录</div>
{% endif %}

{% if trend_data %}
<h2>📈 近 7 天趋势</h2>
<div class="trend-bar">
{% for day in trend_data %}
  <div class="trend-day">
    <div class="trend-fill" style="height: {{ day.height_pct }}%"></div>
    <div class="trend-label">{{ day.label }}</div>
  </div>
{% endfor %}
</div>
{% endif %}

{% if skipped_sample %}
<h2>⏭️ 最近跳过的岗位（抽样）</h2>
<table>
<thead><tr><th>评分</th><th>岗位</th><th>跳过原因</th></tr></thead>
<tbody>
{% for entry in skipped_sample %}
<tr>
  <td><span class="score-badge score-low">{{ entry.score }}</span></td>
  <td>{{ entry.job }}</td>
  <td style="color:#888;font-size:13px">{{ entry.reason }}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% endif %}

</body>
</html>"""


def score_class(score: int) -> str:
    if score >= 50:
        return "score-high"
    elif score >= 30:
        return "score-mid"
    return "score-low"


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

    # Jinja2 可选，这里用简单字符串替换
    html = HTML_TEMPLATE
    replacements = {
        "{{ report_time }}": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "{{ platform }}": "Boss直聘",
        "{{ total_jobs_seen }}": str(merged["applied"].__len__() + merged["skipped"].__len__()),
        "{{ total_applied }}": str(len(merged["applied"])),
        "{{ total_skipped }}": str(len(merged["skipped"])),
        "{{ total_failed }}": str(len(merged["failed"])),
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    # 投递表格行
    if applied:
        rows = ""
        for e in applied:
            rows += (
                f'<tr><td><span class="score-badge {e["score_class"]}">{e["score"]}</span></td>'
                f'<td>{e.get("company","")}</td><td>{e.get("job","")}</td>'
                f'<td>{e.get("salary","")}</td>'
                f'<td style="color:#aaa;font-size:12px">{e.get("_log_file","")}</td></tr>\n'
            )
        html = html.replace(
            '{% for entry in new_applied %}\n<tr>\n<td><span class="score-badge {{ entry.score_class }}">{{ entry.score }}</span></td>\n<td>{{ entry.company }}</td>\n<td>{{ entry.job }}</td>\n<td>{{ entry.salary }}</td>\n<td style="color:#aaa;font-size:12px">{{ entry._log_file }}</td>\n</tr>\n{% endfor %}',
            rows,
        )
        html = html.replace(
            "{% if new_applied %}", ""
        ).replace(
            "{% else %}\n<div class=\"no-data\">暂无投递记录</div>\n{% endif %}",
            ""
        )
    else:
        html = html.replace(
            "{% if new_applied %}", ""
        ).replace(
            "{% else %}",
            ""
        ).replace(
            "{% endif %}",
            ""
        )

    # 趋势图
    if trend_data:
        bars = ""
        for d in trend_data:
            bars += (
                f'<div class="trend-day">'
                f'<div class="trend-fill" style="height: {d["height_pct"]}%"></div>'
                f'<div class="trend-label">{d["label"]}</div></div>\n'
            )
        html = html.replace(
            '{% for day in trend_data %}\n  <div class="trend-day">\n    <div class="trend-fill" style="height: {{ day.height_pct }}%"></div>\n    <div class="trend-label">{{ day.label }}</div>\n  </div>\n{% endfor %}',
            bars,
        )
        html = html.replace("{% if trend_data %}", "").replace("{% endif %}", "")
    else:
        html = html.replace("{% if trend_data %}", "").replace("{% endif %}", "")

    # 跳过抽样
    if skipped:
        rows = ""
        for e in skipped:
            rows += (
                f'<tr><td><span class="score-badge score-low">{e["score"]}</span></td>'
                f'<td>{e.get("job","")}</td><td style="color:#888;font-size:13px">{e.get("reason","")}</td></tr>\n'
            )
        html = html.replace(
            '{% for entry in skipped_sample %}\n<tr>\n<td><span class="score-badge score-low">{{ entry.score }}</span></td>\n<td>{{ entry.job }}</td>\n<td style="color:#888;font-size:13px">{{ entry.reason }}</td>\n</tr>\n{% endfor %}',
            rows,
        )
        html = html.replace("{% if skipped_sample %}", "").replace("{% endif %}", "")
    else:
        html = html.replace("{% if skipped_sample %}", "").replace("{% endif %}", "")

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


if __name__ == "__main__":
    # 直接运行：生成 HTML 报告 + 终端输出
    cfg = load_config()
    print_terminal_summary()
    path = generate_html()
    print(f"\n📄 HTML 报告已生成: {path}")

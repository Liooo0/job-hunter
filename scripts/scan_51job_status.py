#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""51job 回音扫描器（只读）—— 把 51job 的「已查阅 / 感兴趣 / 邀面试 / 谁对我感兴趣」
接进 job-hunter。

背景（2026-09-18 定位）：
  HR 回复扫描的 cron 名字写着「三平台+邮箱」，实际只做 Boss + 邮箱；
  51job 的 read/replied 字段永远是 0 → 「51job 零回复」是观测盲区。
  实际上 51job 把三类信号放在投递记录页的标签里（659 条申请、6 家已标「感兴趣」）。

产出：
  · data/51job_status.json —— 结构化结果（供报表/告警使用）
  · 命中「感兴趣 / 邀面试」时写 data/reply_review.lock（回复优先级 > 投递）
  · 控制台人话报告

只读：只切标签/只读文字，不点「立即沟通」、不发消息、不改简历、不申请。
"""
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, "scripts")
from cdp51 import close_tab, open_tab, read_via_ws  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)          # 让 notify / reply_lock 等根目录模块可导入
OUT = os.path.join(ROOT, "data", "51job_status.json")

APPLY_URL = "https://i.51job.com/userset/my_apply.php"
MYJOB_URL = "https://we.51job.com/pc/my/myjob"

CLICK_TEXT = r"""
(() => {
  const want = "%s";
  const els = [...document.querySelectorAll('a,div,span,li,button')];
  const hit = els.find(e => (e.innerText || '').trim().replace(/\s+/g,'') === want);
  if (!hit) return 'NOT_FOUND';
  hit.click();
  return (hit.tagName + '|' + (hit.href || ''));
})()
"""

PARSE_ROWS = r"""
(() => {
  const t = (document.body.innerText || '').replace(/\s+/g, ' ');
  const out = {rows: [], total: ''};
  let m = t.match(/(社会申请)\s*([0-9]+)\+?/);
  if (m) out.total = m[0];
  t.split(/申请于/).slice(1, 21).forEach(seg => {
    const s = seg.replace(/\s+/g, ' ').trim();
    const date = (s.match(/^\s*(20[0-9]{2}-[0-9]{2}-[0-9]{2})/) || [])[1] || '';
    if (!date) return;                     // 无日期 = 分页脚/广告，丢掉
    const body = s.replace(/^[0-9-]{10}\s*申请简历：[^ ]*\s*/, '').slice(0, 120);
    if (/下载 ?APP|销售热线|版权/.test(body)) return;
    out.rows.push({date: date, text: body});
  });
  return JSON.stringify(out);
})()
"""


def click_and_read(ws, label, wait=4):
    res = read_via_ws(ws, CLICK_TEXT % label, timeout=20)
    time.sleep(wait)
    return res, json.loads(read_via_ws(ws, PARSE_ROWS, timeout=25) or "{}")


result = {"scanned_at": datetime.now().isoformat(timespec="seconds"), "platform": "51job"}

# ── 1) 投递记录：全部 / 已查阅 / 感兴趣 / 邀面试 ──
tab = open_tab(APPLY_URL)
tid, ws = tab.get("id"), tab.get("webSocketDebuggerUrl")
try:
    for _ in range(10):
        time.sleep(2)
        if "my_apply" in str(read_via_ws(ws, "location.href") or ""):
            break
    time.sleep(3)
    base = json.loads(read_via_ws(ws, PARSE_ROWS, timeout=25) or "{}")
    result["total_hint"] = base.get("total") or ""
    print(f"═══ 51job 投递记录：{result['total_hint'] or '（未读到总数）'} ═══")
    for label in ("全部", "已查阅", "感兴趣", "邀面试"):
        res, d = click_and_read(ws, label)
        rows = d.get("rows") or []
        result[label] = {"click": res, "rows": rows}
        print(f"\n── 「{label}」({res}) 列出 {len(rows)} 条")
        for r in rows[:8]:
            print(f"     {r.get('date','')} {r.get('text','')[:96]}")
finally:
    close_tab(tid)

# ── 2) 谁对我感兴趣 ──
print("\n═══ 谁对我感兴趣 ═══")
tab = open_tab(MYJOB_URL)
tid, ws = tab.get("id"), tab.get("webSocketDebuggerUrl")
try:
    for _ in range(8):
        time.sleep(2)
        if "myjob" in str(read_via_ws(ws, "location.href") or ""):
            break
    time.sleep(3)
    res = read_via_ws(ws, CLICK_TEXT % "谁对我感兴趣", timeout=20)
    time.sleep(5)
    href = str(read_via_ws(ws, "location.href") or "")
    body = " ".join((read_via_ws(ws, "document.body.innerText") or "").split())
    print(f"  点击结果: {res}")
    print(f"  落地地址: {href[:110]}")
    # 抓「感兴趣」相关上下文 + 公司名候选
    snippet = body[:900]
    result["who_interested"] = {"click": res, "url": href, "snippet": snippet}
    print(f"  内容: {snippet[:700]}")
    for kw in ("感兴趣", "看过", "浏览"):
        i = body.find(kw)
        if i >= 0:
            print(f"  🔎[{kw}] …{body[max(0,i-80):i+140]}…")
finally:
    close_tab(tid)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\n[已落盘] {OUT}")

# ── 3) 有「感兴趣/邀面试」就推送提醒 ──
# 不上锁：感兴趣只代表 HR 标记了兴趣、并非「有会话待回复」，
# 为此停掉投递会把产量砍掉（用户目标是回复+投递并行）。
# 邀面试才是必须立刻响应的信号 —— 它走告警让用户马上处理。
hot = len(result.get("感兴趣", {}).get("rows") or [])
inv = len(result.get("邀面试", {}).get("rows") or [])
read_n = len(result.get("已查阅", {}).get("rows") or [])
print(f"\n📊 51job 回音：已查阅 {read_n} / 感兴趣 {hot} / 邀面试 {inv}")
if hot or inv:
    try:
        from notify import alert
        lines = []
        for r_ in (result.get("感兴趣") or {}).get("rows", [])[:6]:
            lines.append(f"· {r_.get('date','')} {r_.get('text','')[:70]}")
        for r_ in (result.get("邀面试") or {}).get("rows", [])[:6]:
            lines.append(f"· [邀面试] {r_.get('date','')} {r_.get('text','')[:70]}")
        alert("51job_signal",
              f"51job 有 {hot} 家感兴趣 / {inv} 家邀面试",
              "这些 HR 标记了兴趣但没人跟进过（此前系统从没读过这个页面）：\n" + "\n".join(lines) +
              "\n\n去跟进：https://i.51job.com/userset/my_apply.php",
              level="info", throttle=21600)
        print("  ✅ 已推送提醒")
    except Exception as e:
        print(f"  ⚠️ 提醒推送失败: {e}")
else:
    print("  ℹ️ 无感兴趣/邀面试")
print("[只读扫描结束] 未点击任何「立即沟通」/申请/修改按钮")

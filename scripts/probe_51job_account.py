#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""51job 账号体检（只读，等投递轮结束后自动运行）
检查三件事：
 1) 在线简历：完整度 + 公开状态（若为「保密/隐藏」，HR 看不到你 —— 这是零回复的头号嫌疑）
 2) 投递记录：库里 661 条到底有没有真进 51job 后台
 3) 消息中心：有没有 HR 消息（我们此前从不扫这里）
全程只读：不点申请、不改简历、不发消息。
"""
import os
import re
import subprocess
import time

from DrissionPage import ChromiumOptions, ChromiumPage

WAIT_ROUND = True
READONLY_HINT = os.environ.get("FLOW_READONLY", "1")

# 0) 等投递轮结束，避免与投递争用同一个 Chrome（用户铁律：同一 Chrome 不得并发）
if WAIT_ROUND:
    for i in range(60):                       # 最多等 30 分钟
        n = subprocess.run(["bash", "-lc", 'ps aux | grep -E "platform_51job|boss_apply" | grep -v grep | wc -l'],
                           capture_output=True, text=True).stdout.strip()
        if n == "0":
            print(f"[等待] 投递轮已结束（第 {i} 次检查）")
            break
        print(f"[等待] 投递进行中… {i * 30}s")
        time.sleep(30)
    else:
        print("[等待] 超时，仍可能有轮次在跑 —— 仍以只读方式继续")

co = ChromiumOptions().set_local_port(9223)
page = ChromiumPage(co)


def dump(url, wait=5, label=""):
    tab = page.new_tab()
    try:
        tab.get(url)
        time.sleep(wait)
        title = tab.title
        el = tab.ele("tag:body")
        body = el.text if el else ""
        return title, body or ""
    except Exception as e:
        return "ERR", f"{type(e).__name__}: {e}"
    finally:
        try:
            tab.close()
        except Exception:
            pass


def flat(s, n=400):
    return " ".join(str(s).split())[:n]


print("\n═══ 1. 发现 51job 求职者中心的各个入口 ═══")
t, body = dump("https://we.51job.com/pc/index", 6)
print("  标题:", t)
print("  首页内容:", flat(body, 220))

links = []
tab = page.new_tab()
try:
    tab.get("https://we.51job.com/pc/index")
    time.sleep(5)
    for a in tab.eles("tag:a"):
        try:
            txt = (a.text or "").strip()
            href = a.attr("href") or ""
        except Exception:
            continue
        if txt and href and any(k in txt for k in ("简历", "投递", "申请", "消息", "沟通", "我的")):
            links.append((txt, href))
finally:
    try:
        tab.close()
    except Exception:
        pass
seen = set()
print("\n  发现的入口:")
for txt, href in links:
    key = (txt, href)
    if key in seen:
        continue
    seen.add(key)
    print(f"    {txt[:14]:16} {href[:80]}")

print("\n═══ 2. 在线简历（完整度 / 公开状态）═══")
for url in ("https://i.51job.com/resume/resume.php",
            "https://we.51job.com/pc/resume",
            "https://i.51job.com/userset/my_resume.php",
            "https://m.51job.com/my/resume"):
    t, body = dump(url, 5)
    if t == "ERR":
        print(f"  {url} → {flat(body, 80)}")
        continue
    txt = flat(body, 300)
    print(f"  {url}\n    标题: {t[:46]}\n    内容: {txt}")

print("\n═══ 3. 投递记录（库里 661 条有没有进后台）═══")
for url in ("https://we.51job.com/pc/apply-record",
            "https://i.51job.com/apply/apply_record.php",
            "https://we.51job.com/pc/deliver-record",
            "https://m.51job.com/my/applyrecord"):
    t, body = dump(url, 5)
    if t == "ERR":
        print(f"  {url} → {flat(body, 80)}")
        continue
    print(f"  {url}\n    标题: {t[:46]}\n    内容: {flat(body, 320)}")

print("\n═══ 4. 消息中心（HR 消息 —— 我们此前从没看过这里）═══")
for url in ("https://i.51job.com/message/",
            "https://we.51job.com/pc/message",
            "https://m.51job.com/my/message",
            "https://i.51job.com/im/"):
    t, body = dump(url, 5)
    if t == "ERR":
        print(f"  {url} → {flat(body, 80)}")
        continue
    txt = flat(body, 300)
    print(f"  {url}\n    标题: {t[:46]}\n    内容: {txt}")
    if re.search(r"(HR|招聘|消息|沟通|未读)", txt):
        print("    ⭐ 页面出现 HR/消息类文案，需要人工细看")

print("\n[完成] 只读体检结束；未点击任何申请/修改/发送按钮。")

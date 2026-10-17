#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓指定邮件全文（默认抓东亚银行那封"申请未完成"通知）。"""
import email
import imaplib
import os
import re
import sys
from email.header import decode_header

ENV = os.path.expanduser("~/.gamebanana-monitor.env")
env = {}
if os.path.exists(ENV):
    for line in open(ENV, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")


def dec(s):
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(part)
    return "".join(out)


KEY = sys.argv[1] if len(sys.argv) > 1 else "东亚"

M = imaplib.IMAP4_SSL("imap.qq.com", 993, timeout=30)
M.login(env["QQ_EMAIL_USER"], env["QQ_EMAIL_PASSWORD"])
M.select("INBOX")

typ, data = M.search(None, "ALL")
ids = data[0].split()[-60:]
found = 0
for i in reversed(ids):
    typ, d = M.fetch(i, "(RFC822)")
    if not d or not d[0]:
        continue
    msg = email.message_from_bytes(d[0][1])
    subj = dec(msg.get("Subject"))
    frm = dec(msg.get("From"))
    if KEY not in subj and KEY not in frm:
        continue
    found += 1
    print("=" * 60)
    print("主题:", subj)
    print("发件:", frm)
    print("时间:", msg.get("Date"))
    body = ""
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain":
            body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
            break
        if ct == "text/html" and not body:
            html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
            body = re.sub(r"<[^>]+>", " ", html)
    body = re.sub(r"\s+", " ", body).strip()
    print("正文:")
    print(body[:2500])
    # 抽链接
    links = set()
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
            for m in re.finditer(r'href="(https?://[^"]+)"', html):
                links.add(m.group(1))
    if links:
        print("--- 链接 ---")
        for l in sorted(links):
            print(" ", l[:200])
    print()
    if found >= 3:
        break

if not found:
    print(f"没找到含「{KEY}」的邮件")
M.logout()

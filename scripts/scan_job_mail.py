#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 QQ 邮箱里的招聘相关邮件（ATS 邀约 / HR 联系 / 面试通知）

独立于 Chrome 运行 —— 不占投递浏览器，可以高频跑。
输出：仅打印需要人工处理的邮件摘要（有新邮件才出声），无新邮件静默。
用法：
  python3 scripts/scan_job_mail.py            # 扫最近 20 封
  python3 scripts/scan_job_mail.py --days 3   # 只报最近 3 天的
  python3 scripts/scan_job_mail.py --all      # 显示所有招聘邮件（含已读）
"""
import argparse
import imaplib
import email
import email.utils
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / 'data' / 'mail_scan_state.json'
ENV_FILE = Path.home() / '.gamebanana-monitor.env'

# 招聘相关发件人/域名特征
ATS_DOMAINS = [
    'ajinga.com', 'workday', 'myworkday', 'successfactors', 'taleo',
    'icims', 'greenhouse', 'lever.co', 'smartrecruiters', 'beamery',
    'mokahr', 'jobs.51job', '51job.com', 'zhipin.com', 'liepin.com',
    'lagou.com', 'zhilian', 'recruit', 'talent', 'careers',
]
# 招聘相关主题关键词
JOB_SUBJECT_KW = [
    '申请', '应聘', '面试', '简历', '职位', '岗位', '邀约', '招聘',
    'application', 'interview', 'position', 'candidate', 'offer',
    'resume', 'recruit', 'job', '面试邀约', '在线测评', '笔试',
]
# 明确排除（广告/通知类噪音）
# 2026-09-22：补全角 '（AD）' —— 原表只有半角 '(AD)'，而 51job 营销邮件用的是全角，
# 结果「AI证书培训卖课」邮件被当成招聘邮件推给用户（凌晨 4:05 那次）。
NOISE_KW = ['(AD)', '（AD）', '【AD】', '【广告】', '退订', 'newsletter', '周报', '优惠', '促销',
            '发票', 'receipt', '验证', 'verify', '安全提醒', '储存空间',
            'oaut', 'oauth', 'third-party', 'access to your']

# 营销发件人特征（2026-09-22 新增）—— 招聘平台的营销/EDM 通道，不是招聘沟通
MARKETING_SENDER = ['mkt@', 'marketing@', 'edm@', 'newsletter@', 'mailer@', '-mkt', 'mkt.']

# ── 有截止时间的「申请未完成」提醒（2026-09-23 新增，最高优先级）──────────────
# 为什么单列：这类邮件比面试邀请更急 —— 过期等于机会消失。
# 实名事故：东亚银行的「请于今日完成你在东亚中国的职位」被静默丢弃；
# 汇丰（HSBC）的同类提醒 9/11、9/14 各来一次，同样没被顶到用户面前。
URGENT_PAT = ('申请未完成', '申请尚未完成', '请于今日完成', '今日完成', '完善申请',
              '完成你的申请', '完成您在', '继续完成', '尚未完成')


def is_urgent_mail(subject: str) -> bool:
    """是不是「有截止时间的申请动作项」。"""
    s = subject or ''
    return any(p in s for p in URGENT_PAT)

# 促销正文特征（2026-09-22 新增）—— 命中 2 个及以上才算广告，避免误杀正常邮件
PROMO_BODY = ['添加老师', '领取补贴', '企微添加', '扫码添加', '关注微信公众号',
              '不想再收到此类邮件', '立即查看', '下载前程无忧', '邀您投递简历',
              '好机会别错过', '急招中']
# 发件人黑名单（技术平台/电商/媒体 —— 这些域的"application/申请"不是求职申请）
# 招聘放行表（2026-09-23 新增）—— 命中即视为招聘沟通，**优先于**黑名单与营销判定。
# 为什么需要：银行/大厂的招聘系统常用 do_not_reply / noreply 发件人。
# 实测被静默丢弃的两封：东亚银行「申请未完成」提醒
# （BEAChinaRecruitment_do_not_reply@…）与 TÜV Rheinland 的投递确认 —— 用户只能自己去邮箱里发现。
# 取舍：宁可偶有误放（银行营销信），也不能再丢真人 HR 信。
RECRUIT_ALLOW = [
    'recruit', 'recruitment', 'campus', 'talent', 'career', 'hr@', 'hr.', 'jobs@',
    'zhaopin', 'successfactors', 'workday', '51job', 'liepin', 'zhipin',
    'bea', 'bank', 'tuv', 'tüv',
]

SENDER_BLOCK = [
    'github.com', 'xiaomi', 'mimo', 'google.com', 'apple.com', 'icloud',
    'epicgames', 'roblox', 'commandcode', 'deepseek', 'openai', 'ikuuu',
    'smzdm', 'hpoi', 'steam', 'adobe', 'spotify', 'youtube',
    'notion', 'vercel', 'cloudflare', 'microsoft',
]

# ── 平台自营营销/EDM 通道（2026-09-25 新增，必须先于放行表判定）───────────────
# 起因：放行表（RECRUIT_ALLOW）为防丢银行/ATS 的 do_not_reply 往来信而排在前面，
# 代价是**招聘平台自己的营销邮件**也被放行 —— 实测 51job 的 quickjobs 子域
# 「职位动态推送」（无 AD 标记、主题含「行政/后勤职位要求高度一致」）被判成招聘。
# 只拦「平台域 + 营销特征」的交集：真人 HR、银行/ATS、平台招聘通知一律不动。
# 反例保护：`<service@51job.example.com>` 发「【面试邀请】」必须继续放行。
_PLATFORM_BRANDS = ('51job', 'zhipin', 'liepin', 'lagou', 'zhilian', 'jobui')
_PROMO_LOCAL_MARKS = ('mkt', 'marketing', 'edm', 'newsletter', 'mailer', 'promo')
_PROMO_SUBDOMAINS = ('quickjobs.',)


def _sender_addr(sender: str) -> str:
    """从 `名字 <addr@host>` / `addr@host` 里取出纯地址（小写）。"""
    s = (sender or "").lower()
    if "<" in s and ">" in s:
        s = s[s.rfind("<") + 1: s.rfind(">")]
    return s.strip()


def is_platform_promo_sender(sender: str) -> bool:
    """是不是招聘平台自营的营销/EDM 通道（不是 HR 沟通）。"""
    addr = _sender_addr(sender)
    if "@" not in addr:
        return False
    local, _, domain = addr.rpartition("@")
    if not any(b in domain for b in _PLATFORM_BRANDS):
        return False
    if any(m in local for m in _PROMO_LOCAL_MARKS):
        return True
    return any(sub in domain for sub in _PROMO_SUBDOMAINS)


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def dec(s):
    try:
        parts = decode_header(s or '')
        out = ''
        for t, c in parts:
            if isinstance(t, bytes):
                try:
                    out += t.decode(c or 'utf-8', errors='ignore')
                except Exception:
                    out += t.decode('utf-8', errors='ignore')
            else:
                out += t
        return out
    except Exception:
        return s or ''


def extract_body(msg, limit=1200):
    body = ''
    for part in msg.walk():
        ct = part.get_content_type()
        if ct not in ('text/plain', 'text/html'):
            continue
        raw = part.get_payload(decode=True)
        if not raw:
            continue
        try:
            txt = raw.decode('utf-8')
        except UnicodeDecodeError:
            try:
                txt = raw.decode('gbk', errors='ignore')
            except Exception:
                continue
        if ct == 'text/html':
            txt = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', txt, flags=re.S | re.I)
            txt = re.sub(r'<[^>]+>', ' ', txt)
            txt = re.sub(r'&nbsp;?', ' ', txt)
            txt = re.sub(r'&[a-z]+;', ' ', txt)
        txt = re.sub(r'\s+', ' ', txt).strip()
        if len(txt) > len(body):
            body = txt
    return body[:limit]


def is_job_mail(subject, sender, body):
    """判断是否招聘相关邮件。

    2026-09-11 收紧：'application/申请' 泛词误报过（GitHub OAuth application、
    小米 MiMo 邀测申请）。现在：ATS 域直接算；否则必须正文出现**强招聘词**。
    """
    combo = f"{subject} {sender}"
    if any(n.lower() in combo.lower() for n in NOISE_KW):
        return False, 'noise'
    f_low = sender.lower()
    # 发件人黑名单（技术平台/电商/媒体）
    # 平台自营营销通道必须排在放行表之前（2026-09-25）：放行表含 '51job'/'zhipin' 等
    # 宽词，会把平台自己的 EDM 一并放行；这两条路只能靠"平台域 + 营销特征"区分。
    if is_platform_promo_sender(sender):
        return False, 'platform_marketing_sender'
    # 招聘放行优先（2026-09-23）：银行的招聘系统常用 do_not_reply 发件人，
    # 不能因为发件人长得像"系统通知"就静默丢弃 —— 实测丢过东亚银行的「申请未完成」提醒。
    if any(a in f_low for a in RECRUIT_ALLOW):
        return True, 'recruit_allow'
    if any(b in f_low for b in SENDER_BLOCK):
        return False, 'sender_blocked'
    # ── 营销/广告邮件拦截（2026-09-22 新增，必须放在 ATS 判定之前）──
    # 起因：51job 的营销通道（mkt 与 quickjobs 两个子域）命中 ATS_DOMAINS 里的 '51job.com'，
    # 于是「AI证书培训卖课」和「职位动态推送」被当成招聘邮件推给用户。
    if any(m in f_low for m in MARKETING_SENDER):
        return False, 'marketing_sender'
    body_low = (body or "").lower()
    if sum(1 for p in PROMO_BODY if p.lower() in body_low) >= 2:
        return False, 'promo_body'
    # ATS/招聘平台域名 → 直接算招聘邮件
    if any(d in f_low for d in ATS_DOMAINS):
        return True, 'ATS发件人'
    # 否则：正文必须出现强招聘词（中文强词 或 明确英文招聘语境）
    text = f"{subject} {body}".lower()
    strong_cn = ['应聘', '面试', '简历', '职位', '岗位', '招聘', '入职',
                 '候选人', 'offer', '笔试', '测评', '面试邀约', '期望薪资']
    strong_en = ['interview', 'candidate', 'recruiter', 'your application',
                 'position at', 'job offer', 'schedule a call']
    if any(k in text for k in strong_cn):
        return True, '正文强招聘词'
    if any(k in text for k in strong_en):
        return True, '英文招聘语境'
    return False, ''


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"seen_ids": [], "last_run": None}


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7, help='只报最近 N 天')
    ap.add_argument('--limit', type=int, default=30, help='扫描最近 N 封')
    ap.add_argument('--all', action='store_true', help='显示全部（含已报过的）')
    args = ap.parse_args()

    env = load_env()
    pwd = env.get('QQ_EMAIL_PASSWORD')
    if not pwd:
        print("❌ 未找到 QQ_EMAIL_PASSWORD（~/.gamebanana-monitor.env）")
        return 1
    # 账号也从 env 读（2026-09-15：原来硬编码邮箱，公开仓库里等于泄露 PII）
    user = env.get('QQ_EMAIL_USER')
    if not user:
        print("❌ 未找到 QQ_EMAIL_USER（~/.gamebanana-monitor.env）")
        return 1

    state = load_state()
    seen = set(state.get('seen_ids', []))
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    try:
        M = imaplib.IMAP4_SSL('imap.qq.com', 993, timeout=30)
        M.login(user, pwd)
        M.select('INBOX')
    except Exception as e:
        print(f"❌ 邮箱连接失败: {e}")
        return 1

    typ, data = M.search(None, 'ALL')
    ids = data[0].split()[-args.limit:]

    found = []
    for i in ids[::-1]:
        msg_id = i.decode()
        typ, md = M.fetch(i, '(RFC822)')
        if not md or not md[0]:
            continue
        msg = email.message_from_bytes(md[0][1])
        subj = dec(msg.get('Subject', ''))
        sender = dec(msg.get('From', ''))
        date_hdr = msg.get('Date', '')
        # 时间过滤
        try:
            dt = email.utils.parsedate_to_datetime(date_hdr)
            if dt and dt.tzinfo and dt < cutoff:
                continue
        except Exception:
            pass
        body = extract_body(msg)
        ok, why = is_job_mail(subj, sender, body)
        if not ok:
            continue
        if msg_id in seen and not args.all:
            continue
        found.append({
            'id': msg_id, 'date': date_hdr[:31], 'from': sender,
            'subject': subj, 'why': why, 'body': body[:600],
        })

    M.logout()

    if not found:
        # 无新邮件 → 静默（cron 友好；手动跑时给一行提示）
        if sys.stdout.isatty():
            print("📭 没有新的招聘邮件")
        return 0

    # 最高优先级先顶出来：有截止时间的申请动作项，过期即作废（2026-09-23 新增）
    urgent = [f for f in found if is_urgent_mail(f.get('subject', ''))]
    if urgent:
        print(f"⏰⏰ 有截止时间的申请待完成 {len(urgent)} 封 —— 今天就处理，过期即作废：")
        for f in urgent:
            print(f"   ⏰ {f.get('from', '')[:50]}")
            print(f"      {f.get('subject', '')[:80]}")
        print()

    print(f"📬 发现 {len(found)} 封招聘相关邮件：\n")
    for f in found:
        print("=" * 60)
        print(f"时间: {f['date']}")
        print(f"发件: {f['from'][:70]}")
        print(f"主题: {f['subject'][:90]}")
        print(f"命中: {f['why']}")
        print("正文摘要:")
        print(f"  {f['body'][:500]}")
        print()

    # 记录已报（cron 静默用）
    seen.update(f['id'] for f in found)
    state['seen_ids'] = sorted(seen)[-500:]
    state['last_run'] = datetime.now().isoformat()
    save_state(state)
    print("提示：如需回复，把邮件内容发我，我起草后请你确认再发送。")
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(BASE))
    sys.exit(main())

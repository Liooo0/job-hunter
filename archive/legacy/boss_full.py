#!/usr/bin/env python3
"""
Boss直聘 全自动投递 + 回复
==========================
用法:
  python3 boss_full.py              # 先应用，后扫消息（生成pending）
  python3 boss_full.py --apply-only # 只投递
  python3 boss_full.py --reply-only # 只扫消息+发送
"""

import subprocess, sys, json, time, random, re
from pathlib import Path
from datetime import datetime

SKILL_DIR = Path(__file__).parent
PENDING_DIR = SKILL_DIR / "pending_replies"
SENT_DIR = SKILL_DIR / "sent_replies"
PENDING_DIR.mkdir(parents=True, exist_ok=True)
SENT_DIR.mkdir(parents=True, exist_ok=True)

PERSONA_PATH = SKILL_DIR / "persona.md"


def find_job_log(company_fragment):
    for log_file in sorted(SKILL_DIR.glob("boss-*-log.json")):
        try:
            for entry in json.loads(log_file.read_text()).get("applied", []):
                c = entry.get("company", "") or ""
                if company_fragment and len(company_fragment) >= 4 and company_fragment[:4] in c:
                    return entry
        except Exception:
            continue
    return {}


def run_apply():
    print("\n" + "=" * 60)
    print("  📮 第1步: 自动投递")
    print("=" * 60)
    result = subprocess.run(
        [sys.executable, str(SKILL_DIR / "boss_apply.py"), "--daily"],
        cwd=str(SKILL_DIR)
    )
    return result.returncode == 0


def scan_messages():
    """扫描未读消息，返回HR有回复的列表"""
    print("\n" + "=" * 60)
    print("  💬 第2步: 扫描未读消息")
    print("=" * 60)

    try:
        from DrissionPage import ChromiumPage
    except ImportError:
        print("❌ 需要 DrissionPage")
        return []

    try:
        page = ChromiumPage(9222)
    except Exception as e:
        print(f"❌ Chrome连接失败: {e}")
        return []

    tab = page.new_tab("https://www.zhipin.com/web/geek/chat")
    time.sleep(8)

    result = tab.run_js("""
        var lis = document.querySelectorAll("li");
        var out = [];
        lis.forEach(function(li) {
            var nameBox = li.querySelector(".name-box");
            if (!nameBox) return;
            var data = {nameBox: nameBox.textContent.trim()};
            var badge = li.querySelector(".notice-badge");
            data.unread = badge ? parseInt(badge.textContent.trim()) : 0;
            var timeEl = li.querySelector(".time");
            data.time = timeEl ? timeEl.textContent.trim() : '';
            var nameEl = li.querySelector(".name-text");
            data.name = nameEl ? nameEl.textContent.trim() : '';
            var msgEl = li.querySelector(".last-msg-text");
            data.lastMsg = msgEl ? msgEl.textContent.trim() : '';
            out.push(data);
        });
        return out;
    """)

    # 过滤HR真实回复
    hr_replies = []
    for item in result:
        if item["unread"] < 1:
            continue
        msg = item.get("lastMsg", "")
        if "您好！我是刘文迪" in msg:
            continue
        if "ai应用工程师" in msg:
            continue
        if not msg or len(msg) < 2:
            continue
        # 跳过系统消息
        if msg.startswith("您正在与Boss"):
            continue
        hr_replies.append(item)

    if not hr_replies:
        print("📭 没有新的HR消息")
        return []

    print(f"📋 {len(hr_replies)} 条HR消息\n")

    out = []
    for item in hr_replies:
        name_box = item.get("nameBox", "")
        name = item.get("name", "")
        # 拆分公司名
        company = name_box[len(name):].strip() if name and name_box.startswith(name) else ""
        for kw in ["HR", "hr", "Hr", "主管", "经理", "总监", "专员", "专家",
                    "HRBP", "Hrbp", "hrbp", "人事HRBP", "人事", "招聘专员",
                    "招聘专家", "招聘者", "招聘主管", "创始人", "联合创始人",
                    "HRD", "hrd", "人事经理", "人事主管", "..."]:
            if company.endswith(kw):
                company = company[:-len(kw)].strip()
                break

        job_ctx = find_job_log(company)

        data = {
            "name": name,
            "company": company,
            "name_box": name_box,
            "message": item.get("lastMsg", ""),
            "unread": item.get("unread", 0),
            "time": item.get("time", ""),
            "job_context": dict(job_ctx),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "my_reply": "",
        }
        out.append(data)
        print(f"  [{len(out)}] {name} | {company[:20]} | {data['message'][:60]}")
    print()
    return out


def classify_and_reply(messages, skip_rejects=True):
    """
    根据HR消息自动生成回复。分类逻辑:
    - 要简历 → 答应 + 反问岗位方向
    - 约面试 → 确认时间形式
    - 已拒绝 → 不回复(skip)
    - 模板回执 → 不回复(skip)
    - 其他 → 礼貌回问
    """
    replies = []

    for msg in messages:
        text = msg.get("message", "")
        company = msg.get("company", "")
        name = msg.get("name", "")
        job = msg.get("job_context", {}).get("job_title", "")

        # ── 拒绝类 ──
        reject_kw = ["不合适", "不太匹配", "招够了", "不需要", "暂时不", "已招到",
                     "岗位已关闭", "简历不匹配", "很抱歉", "祝您找到", "祝您早日",
                     "不匹配", "不太合适", "招满", "满了", "停止招聘", "已结束",
                     "本次暂不推进", "综合评估", "不匹配", "感谢关注", "感谢同学"]
        if skip_rejects and any(k in text for k in reject_kw):
            msg["my_reply"] = ""
            msg["skip_reason"] = "拒绝"
            replies.append(msg)
            continue

        # ── 模板回执 ──
        template_kw = ["简历收到", "感谢关注", "评估合适后", "会及时联系",
                       "匹配度不够", "如有需要会联系"]
        if skip_rejects and any(k in text for k in template_kw):
            msg["my_reply"] = ""
            msg["skip_reason"] = "模板回执"
            replies.append(msg)
            continue

        # ── 系统消息 ──
        if text.startswith("您正在与Boss") or text.startswith("您的附件简历"):
            msg["my_reply"] = ""
            msg["skip_reason"] = "系统消息"
            replies.append(msg)
            continue

        # ── 要简历 ──
        if any(k in text for k in ["发一份你的简历", "简历发一下", "简历过来",
                                     "发你的简历", "你的简历发", "附件简历"]):
            msg["my_reply"] = (
                f"好的，我发你简历。方便的话能简单说下这个岗位主要做什么方向吗？"
            )
            replies.append(msg)
            continue

        # ── 约面试 ──
        if any(k in text for k in ["面试", "聊聊", "沟通一下", "有空吗", "方便",
                                     "见个面", "线下", "线上"]):
            if "线下" in text or "面聊" in text:
                msg["my_reply"] = (
                    "好的，请问是什么方向的岗位？方便的话能先简单介绍下吗，"
                    "让我提前了解一下"
                )
            elif "视频" in text or "线上" in text:
                msg["my_reply"] = "可以的，什么时间方便？另外能简单说下岗位方向吗"
            else:
                msg["my_reply"] = (
                    "好的，请问具体是什么时间？另外能简单说下这个岗位的方向吗，"
                    "让我提前有点准备"
                )
            replies.append(msg)
            continue

        # ── 打招呼/通用 ──
        if any(k in text for k in ["你好", "您好", "hello", "hi", "在吗",
                                     "还在找工作吗", "还在看机会"]):
            msg["my_reply"] = (
                "你好，还在看机会。方便的话能简单介绍下这个岗位的方向和要求吗？"
            )
            replies.append(msg)
            continue

        # ── 默认: 礼貌回问 ──
        msg["my_reply"] = "好的，方便发一下具体的岗位JD吗？我想看下是否匹配"
        replies.append(msg)

    return replies


def send_replies():
    """发送所有有my_reply且status=pending的消息 — 每次最多5条，条间≥30秒"""
    files = sorted(PENDING_DIR.glob("*.json"))
    ready = []
    for f in files:
        try:
            d = json.loads(f.read_text())
            if d.get("my_reply") and d.get("status") == "pending":
                ready.append((f, d))
        except Exception:
            continue

    if not ready:
        print("📭 没有待发送的回复")
        return

    # 风控: 每次最多5条
    MAX_PER_RUN = 5
    if len(ready) > MAX_PER_RUN:
        print(f"⚠️  {len(ready)} 条待发，风控限制只发 {MAX_PER_RUN} 条")
        ready = ready[:MAX_PER_RUN]

    try:
        from DrissionPage import ChromiumPage
        page = ChromiumPage(9222)
    except Exception as e:
        print(f"❌ Chrome连接失败: {e}")
        return

    print(f"📤 发送 {len(ready)} 条回复\n")

    for filepath, data in ready:
        reply = data["my_reply"]
        company = data["company"]
        name = data["name"]
        name_box = data.get("name_box", "")

        print(f"  ✉️  {name} | {company[:25]}")

        # 风控: 条间休息 30-45 秒（模拟人类看消息+思考+打字）
        import random as _random
        wait = 30 + _random.uniform(0, 15)
        print(f"     ⏳ 休息 {wait:.0f}s ...")
        time.sleep(wait)

        tab = page.new_tab("https://www.zhipin.com/web/geek/chat")
        time.sleep(4 + _random.uniform(0, 2))

        # 找并点击聊天
        search = (name_box or company)[:8].replace("'", "\\'")
        clicked = tab.run_js(f"""
            var lis = document.querySelectorAll("li");
            for (var i = 0; i < lis.length; i++) {{
                var txt = lis[i].textContent || '';
                if (txt.indexOf("{search}") !== -1 && txt.length > 15) {{
                    lis[i].click(); return true;
                }}
            }}
            return false;
        """)

        if not clicked:
            for li in tab.eles("tag:li"):
                if search[:4] in (li.text or ""):
                    li.click()
                    clicked = True
                    break

        if not clicked:
            print(f"     ❌ 未找到聊天")
            continue

        time.sleep(3.5 + random.uniform(0, 1))

        # 纯JS输入+发送
        safe_reply = reply.replace("'", "\\'").replace("\n", "\\n")
        result = tab.run_js(f"""
            var textarea = document.querySelector('textarea.input');
            if (!textarea) return 'no_textarea';
            textarea.focus();
            var setter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value').set;
            setter.call(textarea, '{safe_reply}');
            textarea.dispatchEvent(new Event('input', {{bubbles: true}}));
            return 'ok';
        """)

        if result != 'ok':
            print(f"     ❌ 输入失败")
            continue

        time.sleep(0.5)

        send_result = tab.run_js("""
            var btn = document.querySelector('.btn-sure-v2');
            if (!btn) return 'no_btn';
            if (btn.classList.contains('disabled')) return 'disabled';
            btn.click();
            return 'sent';
        """)

        if send_result == 'sent':
            print(f"     ✅ {reply[:50]}...")
            data["status"] = "sent"
            data["sent_at"] = datetime.now().isoformat()
            dest = SENT_DIR / filepath.name
            filepath.rename(dest)
        else:
            print(f"     ⚠️ 发送失败: {send_result}")

        time.sleep(2 + random.uniform(0, 1))

    print(f"\n✅ 回复发送完成")


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--apply-only", action="store_true")
    p.add_argument("--reply-only", action="store_true")
    p.add_argument("--no-reply", action="store_true")
    args = p.parse_args()

    if not args.reply_only:
        ok = run_apply()
        if not ok:
            print("⚠️ 投递有错误，但继续扫描消息...")

    if not args.apply_only and not args.no_reply:
        messages = scan_messages()

        if messages:
            print("=" * 60)
            print("  🤖 第3步: 智能生成回复")
            print("=" * 60)

            replies = classify_and_reply(messages)

            for r in replies:
                fid = re.sub(r"[^\w一-鿿-]", "_",
                             f"{r['company']}_{r['name']}")[:60]
                fpath = PENDING_DIR / f"{fid}.json"
                fpath.write_text(json.dumps(r, ensure_ascii=False, indent=2))

                if r.get("my_reply"):
                    print(f"  ✅ {r['name']:8s} → {r['my_reply'][:50]}...")
                else:
                    print(f"  ⏭️  {r['name']:8s} → 跳过({r.get('skip_reason','')})")

            send_replies()
        else:
            print("\n✅ 投递完成，无待回复消息")

    print("\n" + "=" * 60)
    print("  🏁 全部完成")
    print("=" * 60)


if __name__ == "__main__":
    main()

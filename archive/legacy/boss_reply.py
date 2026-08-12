#!/usr/bin/env python3
"""
Boss直聘自动回复系统
=====================
用法:
  python3 boss_reply.py          # 扫描未读 → 写入 pending JSON
  python3 boss_reply.py --send   # 发送回复
"""

import argparse, json, re, sys, time, random
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).parent
PENDING_DIR = SKILL_DIR / "pending_replies"
SENT_DIR = SKILL_DIR / "sent_replies"
PENDING_DIR.mkdir(parents=True, exist_ok=True)
SENT_DIR.mkdir(parents=True, exist_ok=True)

from DrissionPage import ChromiumPage


def find_job_log(company_fragment):
    """从投递日志匹配岗位"""
    for log_file in sorted(SKILL_DIR.glob("boss-*-log.json")):
        try:
            for entry in json.loads(log_file.read_text()).get("applied", []):
                c = entry.get("company", "") or ""
                if company_fragment and len(company_fragment) >= 4 and company_fragment[:4] in c:
                    return entry
        except Exception:
            continue
    return {}


def check():
    print("🔍 连接 Chrome ...")
    try:
        page = ChromiumPage(9222)
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return

    tab = page.new_tab("https://www.zhipin.com/web/geek/chat")
    time.sleep(8)

    # JS 直接提取结构化聊天数据
    result = tab.run_js("""
        var lis = document.querySelectorAll("li");
        var out = [];
        lis.forEach(function(li, idx) {
            var nameBox = li.querySelector(".name-box");
            if (!nameBox) return;

            var data = {idx: idx, nameBox: nameBox.textContent.trim()};

            var badge = li.querySelector(".notice-badge");
            data.unread = badge ? parseInt(badge.textContent.trim()) : 0;

            var timeEl = li.querySelector(".time");
            data.time = timeEl ? timeEl.textContent.trim() : "";

            var nameEl = li.querySelector(".name-text");
            data.name = nameEl ? nameEl.textContent.trim() : "";

            var msgEl = li.querySelector(".last-msg-text");
            data.lastMsg = msgEl ? msgEl.textContent.trim() : "";

            var statusEl = li.querySelector(".message-status");
            data.status = statusEl ? statusEl.textContent.trim() : "";

            out.push(data);
        });
        return out;
    """)

    # 过滤：HR 有实际回复的（未读>0 且 消息不是自己的招呼语）
    hr_replies = []
    for item in result:
        if item["unread"] > 0:
            msg = item.get("lastMsg", "")
            if msg and "您好！我是刘文迪" not in msg and "ai应用工程师" not in msg:
                hr_replies.append(item)

    hr_replies_deduped = []
    seen = set()
    for item in hr_replies:
        key = item.get("nameBox", "")[:30]
        if key not in seen:
            seen.add(key)
            hr_replies_deduped.append(item)

    print(f"📋 聊天列表: {len(result)} 条 | HR有回复: {len(hr_replies_deduped)} 条\n")

    if not hr_replies_deduped:
        print("📭 没有新的HR消息")
        return

    for i, item in enumerate(hr_replies_deduped):
        # 从 nameBox 解析公司名（nameBox = "姓名 公司名 角色"）
        name_box = item.get("nameBox", "")
        name = item.get("name", "")
        # 公司名 = nameBox 去掉名字和末尾角色词
        company = name_box[len(name):].strip() if name_box.startswith(name) else ""
        # 去除末尾角色关键词
        role_kw = ["HR","hr","Hr","主管","经理","总监","专员","专家","Hrbp","HRBP",
                   "人事HRBP","人事","招聘专员","招聘专家","招聘者","招聘主管",
                   "创始人","联合创始人","HRD","hrd","人事经理","人事主管","..."]
        for kw in role_kw:
            if company.endswith(kw):
                company = company[:-len(kw)].strip()
                break

        print(f"[{i+1}] {name} | {company[:25]} | {item['time']}")
        print(f"    💬 {item.get('lastMsg','')[:100]}")
        print(f"    未读: {item['unread']} | 状态: {item.get('status','')}")

        job_ctx = find_job_log(company)

        file_id = re.sub(r"[^\w一-鿿-]", "_", f"{company}_{name}")[:60]
        data = {
            "id": file_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "name": name,
            "company": company,
            "name_box": name_box,
            "message": item.get("lastMsg", ""),
            "unread": item["unread"],
            "time": item.get("time", ""),
            "msg_status": item.get("status", ""),
            "job_context": {
                "salary": job_ctx.get("salary", ""),
                "city": job_ctx.get("city", ""),
                "score": job_ctx.get("score", 0),
                "keyword": job_ctx.get("keyword", ""),
                "applied_at": job_ctx.get("time", ""),
                "job_title": job_ctx.get("job", ""),
            },
            "my_reply": "",
        }
        filepath = PENDING_DIR / f"{file_id}.json"
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"    ✅ → {filepath.name}")

    print(f"\n{'='*50}")
    print(f"📬 {len(hr_replies_deduped)} 条消息 → {PENDING_DIR}")
    print(f"📝 跟我说 '回复HR'，我来逐步生成回复")
    print(f"📤 然后运行: python3 ~/projects/job-hunter/boss_reply.py --send")
    print(f"{'='*50}")


def send():
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

    print(f"📤 {len(ready)} 条待发送\n")
    try:
        page = ChromiumPage(9222)
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return

    for filepath, data in ready:
        reply, company, name, name_box = (
            data["my_reply"], data["company"], data["name"], data.get("name_box", ""))
        print(f"✉️  {name} | {company}")

        tab = page.new_tab("https://www.zhipin.com/web/geek/chat")
        time.sleep(4)

        # JS查找并点击（用 nameBox 片段匹配）
        search = (name_box or company)[:8]
        clicked = tab.run_js(f"""
            var lis = document.querySelectorAll("li");
            for (var i = 0; i < lis.length; i++) {{
                var txt = lis[i].textContent || "";
                if (txt.indexOf("{search}") !== -1 && txt.length > 15) {{
                    lis[i].click();
                    return true;
                }}
            }}
            return false;
        """)
        if not clicked:
            # 备用: 直接滚动找
            for li in tab.eles("tag:li"):
                if search[:4] in (li.text or ""):
                    li.click()
                    clicked = True
                    break
        if not clicked:
            print(f"   ❌ 未找到聊天")
            continue

        time.sleep(3 + random.uniform(0, 1))

        # BOSS聊天输入框 - 用纯JS直接操作textarea + 点击发送
        sent = tab.run_js(f"""
            var textarea = document.querySelector('textarea.input');
            if (!textarea) return 'no_textarea';

            // 聚焦并设置值（触发Vue响应）
            textarea.focus();
            var reply = {json.dumps(reply)};

            // 用原生方式逐字输入触发input事件
            textarea.value = '';
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value').set;
            nativeInputValueSetter.call(textarea, reply);
            textarea.dispatchEvent(new Event('input', {{bubbles: true}}));

            // 等待按钮激活
            return 'ok';
        """)

        if sent == 'no_textarea':
            print("   ❌ 无输入框")
            continue

        time.sleep(0.5)

        # 点发送
        sent = tab.run_js("""
            var btn = document.querySelector('.btn-sure-v2');
            if (!btn) return 'no_btn';
            if (btn.classList.contains('disabled')) return 'disabled';
            btn.click();
            return 'sent';
        """)

        print(f"   ✅ 已发送: {reply[:50]}..." if sent == 'sent' else f"   ⚠️ 发送状态: {sent}")
        data["status"] = "sent"
        data["sent_at"] = datetime.now().isoformat()
        dest = SENT_DIR / filepath.name
        filepath.rename(dest)
        time.sleep(2 + random.uniform(0, 1))

    print(f"\n✅ 完成！已发送 {len(ready)} 条")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Boss直聘自动回复")
    p.add_argument("--send", action="store_true")
    args = p.parse_args()
    send() if args.send else check()

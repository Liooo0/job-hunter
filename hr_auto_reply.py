#!/usr/bin/env python3
"""
HR 消息自动回复 — LLM 判断 + 个性化回复
======================================
- 有兴趣的消息（要简历/约面试/问经历）→ LLM 基于你的真实背景生成回复
- 拒绝/模板/系统消息 → 礼貌回一句"多谢回复"（15-30字，措辞轮换）
- 绝不编造简历上没有的经历

用法:
    python3 hr_auto_reply.py            # 扫描 + 生成预览（不发送）
    python3 hr_auto_reply.py --send     # 扫描 + 生成 + 自动发送（≤5条/次，30-45s间隔）
    python3 hr_auto_reply.py --force    # 跳过 pending_replies 中已存在且未发送的
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

SKILL_DIR = Path(__file__).parent

# ─── LLM 配置（复用 Hermes 的 opencode-go 中转）───
BASE_URL = "https://opencode.ai/zen/go/v1"
MODEL = "deepseek-v4-flash"  # 便宜，适合大量回复
MAX_TOKENS = 500

def get_api_key() -> str:
    """从 ~/.hermes/.env 读取 OPENCODE_GO_API_KEY"""
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENCODE_GO_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.getenv("OPENCODE_GO_API_KEY", "")


# ─── 用户背景（真实，仅用于生成回复上下文）───
MY_PROFILE = """我是刘文迪，求职方向：AI应用工程师。base深圳（不是上海/成都，异地岗位要如实说明，除非真能接受再谈）。
学历：2025年本科毕业（广东科技学院·工商管理），往届生，不是在校生，不符合27届/26届校招。
背景：移动通信+工商管理复合背景。
真实项目：
1. BOSS直聘助手（Chrome扩展）：AI生成个性化招呼语、聊天辅助回复、岗位管理面板
2. 95分球鞋监控（Python）：API逆向、关键词粗筛+视觉LLM精筛两级过滤、异步并发、SQLite去重、Webhook推送
3. 求职自动化（Python+DrissionPage）：多平台自动投递、HR消息智能分类、反检测设计
4. RAG匹配引擎（Chroma+BGE）：简历向量化、JD语义检索、本地embedding
技能：Python、LLM API集成、Prompt Engineering、浏览器自动化、数据管道、Linux/Shell。
注意：没有做过短视频/短剧，没有直播带货经历，没有企业级大厂工作经历。"""

# 硬性事实护栏：任何回复不得违反（生成后强制校验）
FACT_GUARDRAILS = [
    ("上海", "我在上海", "我在上海的", "base上海", "在上海的"),
    ("27届", "27届校招的话我符合", "我是27届", "符合27届", "27届"),
    ("26届", "26届校招的话我符合", "我是26届", "符合26届", "26届"),
    ("发到您邮箱", "马上把简历发到您邮箱", "已经发送到您的邮箱", "简历已发送", "已发到邮箱"),
    ("成都在看", "我在看成都的机会", "我目前在看成都"),
]

# 拒绝回复措辞池（轮换，避免千篇一律）
THANKS_POOL = [
    "好的，谢谢您告知，祝您招聘顺利！",
    "收到，感谢您的反馈，祝您早日找到合适的人选！",
    "好的，谢谢您，有合适的机会欢迎再联系我！",
    "了解，谢谢您！祝工作顺利！",
    "好的，谢谢您的回复，祝您这边一切顺利！",
]


def call_llm(messages: list[dict]) -> str:
    """调用 opencode 中转 API"""
    key = get_api_key()
    if not key:
        print("❌ 未找到 OPENCODE_GO_API_KEY")
        sys.exit(1)
    resp = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.6,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def build_reply(msg_text: str, job_title: str, company: str) -> tuple[str, str]:
    """
    返回 (kind, reply)
    kind: interest(有兴趣，认真回) / reject(拒绝，礼貌回)
    """
    prompt = f"""你是刘文迪的求职助理。刘文迪正在Boss直聘找工作。

【刘文迪的真实背景】
{MY_PROFILE}

【规则】
1. 判断这条HR消息是"有兴趣"还是"拒绝/无意义"：
   - 有兴趣：要简历、约面试、问经历/技能/作品、说"合适""聊聊""看下简历"等
   - 拒绝/无意义：不合适、不匹配、招满了、暂不推进、系统提示、模板回执
2. 有兴趣 → 用刘文迪的真实背景写回复（80-150字）：
   - 口语自然，像真人聊天，不要"尊敬的"式模板
   - 只提简历里真实存在的经历，绝不编造
   - 呼应HR提到的点（岗位、技能、问题）
3. 拒绝/无意义 → 简短礼貌回复（15-30字），如"好的，谢谢您告知，祝您招聘顺利！"

【当前消息】
公司: {company}
岗位: {job_title or '未知'}
HR说: {msg_text[:300]}

只输出JSON：{{"kind": "interest" 或 "reject", "reply": "回复内容"}}"""

    try:
        out = call_llm([{"role": "user", "content": prompt}])
        # 提取 JSON
        m = re.search(r'\{.*\}', out, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            kind = "interest" if data.get("kind") == "interest" else "reject"
            reply = data.get("reply", "").strip()
            if reply:
                # 事实护栏：拦截编造内容的回复（降级为礼貌回复或空）
                for guard in FACT_GUARDRAILS:
                    if any(g in reply for g in guard[1:]):
                        print(f"   🛡️ 护栏拦截: 回复含'{guard[0]}'相关编造 → 降级为礼貌回复")
                        return "reject", random.choice(THANKS_POOL)
                return kind, reply
    except Exception as e:
        print(f"⚠️ LLM 调用失败: {e}")

    # 降级：关键词兜底
    REJECT_HINTS = ["不合适", "不匹配", "招够", "暂不", "感谢关注", "祝您", "抱歉",
                    "不好意思", "背景不同", "资质", "暂时不", "已招到", "不太"]
    if any(k in msg_text for k in REJECT_HINTS):
        return "reject", random.choice(THANKS_POOL)
    return "interest", ""  # 空则跳过发送


def _is_hr_real_message(msg: str) -> bool:
    """过滤非 HR 真实消息（自己发的招呼语/礼貌回复、系统占位、系统消息、Boss 广告）。"""
    if not msg or len(msg) < 2:
        return False
    # 自己发的招呼语
    if "您好！我是刘文迪" in msg or "我是刘文迪" in msg:
        return False
    # 自己发的礼貌回复（防重复回）
    if any(msg.startswith(p) for p in [
        "好的，谢谢您", "好的，感谢", "收到，感谢", "收到，谢谢",
        "了解，谢谢您", "了解，感谢", "抱歉，我没有", "不好意思，我没有",
        "好的，谢谢您的回复", "好的，谢谢您的反馈",
    ]):
        return False
    # 系统占位消息（"您正在与BossX沟通"）
    if msg.startswith("您正在与Boss") or msg.startswith("您正在与boss"):
        return False
    # 系统消息
    if msg.startswith("您的附件简历") or "撤回了一条消息" in msg:
        return False
    # Boss 广告/系统推送
    if "职位竞争者" in msg or "查看详细分析" in msg:
        return False
    return True


def _scan_chat_page(unread_only: bool = False) -> list:
    """
    扫描 Boss 聊天页会话列表，返回 HR 真实消息列表。
    unread_only=True 时只保留有未读角标的会话（P2-T3：原 archive/legacy/boss_full.py
    的 scan_messages 行为内联至此——该模块已归档，import 会直接 ModuleNotFoundError）。
    """
    from DrissionPage import ChromiumPage
    import time as _t
    try:
        page = ChromiumPage(9222)
    except Exception as e:
        print(f"❌ Chrome连接失败: {e}")
        return []

    tab = None
    for tid in page.tab_ids:
        try:
            tb = page.get_tab(tid)
            if "zhipin.com/web/geek/chat" in (tb.url or ""):
                tab = tb
                break
        except Exception:
            continue
    if tab is None:
        tab = page.new_tab("https://www.zhipin.com/web/geek/chat")
        _t.sleep(6)

    # 先滚动到底部，确保列表全加载
    for _ in range(3):
        tab.run_js("window.scrollTo(0, document.body.scrollHeight)")
        _t.sleep(0.8)

    result = tab.run_js('''
        var lis = document.querySelectorAll("li");
        var out = [];
        lis.forEach(function(li) {
            var nameBox = li.querySelector(".name-box");
            if (!nameBox) return;
            var badge = li.querySelector(".notice-badge");
            var msgEl = li.querySelector(".last-msg-text");
            var timeEl = li.querySelector(".time");
            out.push({
                nameBox: nameBox.textContent.trim().slice(0, 30),
                unread: badge ? parseInt(badge.textContent.trim()) || 0 : 0,
                lastMsg: msgEl ? msgEl.textContent.trim() : '',
                time: timeEl ? timeEl.textContent.trim() : ''
            });
        });
        return out;
    ''')

    # 过滤：只保留 HR 真实消息；未读模式额外要求角标 ≥ 1
    hr_replies = []
    for item in result:
        if unread_only and item.get("unread", 0) < 1:
            continue
        msg = item.get("lastMsg", "")
        if not _is_hr_real_message(msg):
            continue

        hr_replies.append({
            "company": _extract_company(item["nameBox"]),
            "name": item["nameBox"][:12],
            "nameBox": item["nameBox"],
            "message": msg,
            "unread": item.get("unread", 0),
            "time": item.get("time", ""),
            "job_context": {},
        })
    return hr_replies


def scan_all_conversations():
    """
    全量扫描所有会话：找出最后一条消息是 HR 真实消息（非系统占位/非自己发的招呼语）的会话。
    不依赖未读标记——即使被点开过也能扫到。
    """
    return _scan_chat_page(unread_only=False)


def scan_unread_messages():
    """扫描未读消息（只保留有未读角标的会话）。替代已归档 boss_full.scan_messages。"""
    return _scan_chat_page(unread_only=True)


def _extract_company(name_box: str) -> str:
    """从 '名字+公司名+职位' 里拆公司名（粗略）"""
    # Boss格式通常是: 姓名+公司名+岗位 连在一起
    # 从第2个字符开始尝试找常见后缀
    s = name_box.strip()
    return s[:18] or s


def main():
    send = "--send" in sys.argv
    all_mode = "--all" in sys.argv
    sys.path.insert(0, str(SKILL_DIR))

    if all_mode:
        print("🔍 全量扫描所有会话（不依赖未读标记）...")
        msgs = scan_all_conversations()
    else:
        print("🔍 扫描未读消息...")
        msgs = scan_unread_messages()

    if not msgs:
        print("📭 没有新消息")
        return

    print(f"\n📋 {len(msgs)} 条消息，开始 LLM 分析...\n")
    results = []
    for i, m in enumerate(msgs):
        company = m.get("company", "")
        name = m.get("name", "")
        text = m.get("message", "")
        job = m.get("job_context", {}).get("job_title", "")

        kind, reply = build_reply(text, job, company)
        results.append({"msg": m, "kind": kind, "reply": reply})

        icon = "💬" if kind == "interest" else "⏭️"
        print(f"{icon} [{i+1}/{len(msgs)}] {company[:14]} | {name}")
        print(f"    HR: {text[:70]}")
        print(f"    回复({kind}): {reply[:80]}")
        print()

    interests = [r for r in results if r["kind"] == "interest"]
    rejects = [r for r in results if r["kind"] == "reject"]
    print(f"📊 有兴趣: {len(interests)} | 拒绝礼貌回: {len(rejects)}")

    if not send:
        print("\nℹ️  预览模式，未发送。加 --send 自动发送。")
        return

    # ── 发送模式 ──
    send_safely(results)

    # ── 值守模式：--watch 持续盯新消息 ──
    if "--watch" in sys.argv:
        rounds = SCAN_ROUNDS
        try:
            idx = sys.argv.index("--watch")
            if idx + 1 < len(sys.argv):
                rounds = max(1, int(sys.argv[idx + 1]))
        except (ValueError, IndexError):
            pass
        print(f"\n👁️  值守模式: 每 {SCAN_INTERVAL//60} 分钟扫一次，共 {rounds} 轮")
        for rnd in range(1, rounds + 1):
            print(f"\n⏸️  第 {rnd}/{rounds} 轮前休息 {SCAN_INTERVAL//60} 分钟...")
            time.sleep(SCAN_INTERVAL)
            print(f"\n🔄 第 {rnd} 轮扫描...")
            try:
                msgs = scan_unread_messages()
            except Exception as e:
                print(f"❌ 扫描失败: {e}")
                continue
            if not msgs:
                print("📭 无新消息")
                continue
            new_results = []
            for m in msgs:
                kind, reply = build_reply(
                    m.get("message", ""),
                    m.get("job_context", {}).get("job_title", ""),
                    m.get("company", ""),
                )
                new_results.append({"msg": m, "kind": kind, "reply": reply})
                print(f"{'💬' if kind=='interest' else '⏭️'} {m.get('company','')[:14]} | 回复: {reply[:60]}")
            send_safely(new_results, batch=WATCH_BATCH)
        print("\n🏁 值守结束")
SEND_BATCH = 999        # 单次运行发完全部（间隔 75-105s 已足够保守）
SEND_GAP = (75, 105)    # 条间等待 75-105 秒
SCAN_INTERVAL = 15 * 60  # 扫描间隔 15 分钟
SCAN_ROUNDS = 4          # 默认盯 1 小时（4 轮）
WATCH_BATCH = 3          # 值守模式每轮最多 3 条

def send_one(page, name_box: str, reply: str):
    """
    保守发送单条回复（验证过的方案）：
    1. 复用现有聊天tab，点击目标会话（.name-box）
    2. 聚焦 contenteditable 输入框，execCommand 输入
    3. 派发 keydown Enter（Boss 网页版 Enter=发送）
    4. 验证输入框清空 = 发送成功
    """
    import random as _r
    from DrissionPage import ChromiumPage
    try:
        page = ChromiumPage(9222)
    except Exception as e:
        print(f"❌ Chrome 连接失败: {e}")
        return False

    # 复用已打开的聊天 tab（不开新 tab，降低风控）
    tab = None
    for tid in page.tab_ids:
        try:
            tb = page.get_tab(tid)
            if "zhipin.com/web/geek/chat" in (tb.url or ""):
                tab = tb
                break
        except Exception:
            continue
    if tab is None:
        try:
            tab = page.new_tab("https://www.zhipin.com/web/geek/chat")
            time.sleep(6)
        except Exception as e:
            print(f"❌ 打开聊天页失败: {e}")
            return False

    # 1. 点击目标会话（用 name-box 精确点击）
    search = (name_box or "")[:10]
    clicked = False
    try:
        for li in tab.eles("tag:li"):
            txt = li.text or ""
            if search and search in txt and len(txt) > 15:
                nb = li.ele("css:.name-box", timeout=2)
                if nb:
                    nb.click()
                    clicked = True
                    break
    except Exception:
        pass
    if not clicked:
        print(f"   ⚠️ 未找到会话: {search}")
        return False

    time.sleep(3)

    # 2. 聚焦输入框 + 输入
    r = tab.run_js(f"""
        var ed = document.querySelector('[contenteditable="true"]');
        if (!ed) return 'no_ed';
        ed.focus();
        document.execCommand('selectAll', false, null);
        document.execCommand('insertText', false, {json.dumps(reply)});
        return ed.textContent;
    """)
    if r == "no_ed":
        print("   ⚠️ 找不到输入框")
        return False
    time.sleep(1.2)

    # 3. 派发 Enter 发送
    tab.run_js("""
        var ed = document.querySelector('[contenteditable="true"]');
        ed.focus();
        ed.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
            bubbles: true, cancelable: true
        }));
    """)
    time.sleep(2)

    # 4. 验证：输入框清空 = 发送成功
    cleared = tab.run_js("""
        var ed = document.querySelector('[contenteditable="true"]');
        return ed ? ed.textContent.trim() === '' : false;
    """)
    return bool(cleared)


def send_safely(results: list[dict], batch: int = None):
    """保守发送：条间 75-105 秒，batch 限制每轮条数"""
    import random as _r
    batch = batch or SEND_BATCH
    todo = [r for r in results if r.get("reply")]
    if not todo:
        print("📭 没有要发送的")
        return 0

    sent = 0
    for i, r in enumerate(todo):
        if i >= batch:
            print(f"⚠️ 本轮上限 {batch} 条，剩余 {len(todo)-i} 条下轮再发")
            break
        company = r["msg"].get("company", "")
        name = r["msg"].get("name", "")
        name_box = r["msg"].get("nameBox", "")
        if not name_box:
            name_box = (name or "") + (company or "")
        reply = r["reply"]

        print(f"📤 [{i+1}] {company[:14]} → {reply[:50]}...")
        ok = send_one(None, name_box, reply)
        if ok:
            sent += 1
            print(f"   ✅ 已发送")
        else:
            print(f"   ⚠️ 发送失败（可能已读/找不到聊天）")

        if i < len(todo) - 1:
            wait = _r.uniform(*SEND_GAP)
            print(f"⏳ 保守等待 {wait:.0f}s ...")
            time.sleep(wait)

    print(f"\n✅ 本轮发送 {sent} 条")
    return sent


if __name__ == "__main__":
    main()

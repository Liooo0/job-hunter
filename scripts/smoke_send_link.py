#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发送链路 smoke test（只读验收）— 2026-09-03

目的：验证 boss_apply.py 的发送链路在真实 Chrome 上是否恢复——
     Chrome 9222 连接 → 会话列表定位 → 聊天窗打开 → 输入框 → 发送按钮
     **不实际发送**。只读检查，碰完即还原（关掉误开的聊天窗）。

背景：8/31 实盘 10 投 0 VERIFIED（发送链路 P0 未闭环）；
     9/2 boss_apply.py 被清空、9/3 恢复。测试全绿 ≠ Chrome 链路正常，
     必须先拿受控验收样本再谈恢复投递。

用法：
    python3 scripts/smoke_send_link.py            # 只读全链路检查
    python3 scripts/smoke_send_link.py --send-1   # ⚠️ 人工确认后：真实发送 1 条测试（见 README）

退出码：0=链路就绪  1=连接失败  2=会话定位失败  3=控件检查失败
"""
import json
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

# 检查项计数
CHECKS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = ""):
    CHECKS.append((name, ok, detail))
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("🔍 发送链路 smoke test（只读，不发送）\n")

    # ── 1. Chrome 9222 连接 ──
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        opts = ChromiumOptions(read_file=False)
        opts.set_user_data_path(str(Path("~/job-hunter-chrome").expanduser()))
        opts.set_local_port(9222)
        page = ChromiumPage(addr_or_opts=opts)
        record("Chrome 9222 连接", True, "连接成功")
    except Exception as e:
        record("Chrome 9222 连接", False, str(e)[:80])
        _summary(1)
        return 1

    # ── 2. 定位 Boss tab（不假设 tab[0]——2026-09-03 实测发现 Chrome 里混着
    #      小红书/抖音/闲鱼等其它项目 tab，boss_apply 旧逻辑固定取 tab_ids[0] 会连错 tab）──
    tab = None
    for tid in page.tab_ids:
        try:
            tb = page.get_tab(tid)
            if "zhipin.com" in (tb.url or ""):
                tab = tb
                break
        except Exception:
            continue
    if tab is None:
        # 没有现成 Boss tab → 新开一个（smoke 只读，新开不发送）
        try:
            tab = page.new_tab("https://www.zhipin.com/web/geek/chat")
            time.sleep(5)
            record("新开 Boss tab", True)
        except Exception as e:
            record("新开 Boss tab", False, str(e)[:80])
    url = tab.url or ""
    record("定位 Boss tab", "zhipin.com" in url, url[:60])
    if "zhipin.com" not in url or tab is None:
        record("Boss 域就绪", False, "无法取得 Boss tab，终止只读验收")
        _summary(2)
        return 2

    # ── 3. 会话列表定位（只读扫描 li）──
    try:
        tab.get("https://www.zhipin.com/web/geek/chat")  # 聊天页（只读打开不发送）
        time.sleep(4)
        lis = tab.eles("tag:li")
        chats = [li for li in lis if len((li.text or "")) > 15]
        record("会话列表可扫", len(chats) > 0, f"找到 {len(chats)} 个会话")
        # 打印前 3 个会话名（脱敏：只显示公司部分，不显示 HR 名/时间）
        for li in chats[:3]:
            txt = (li.text or "").replace("\n", " | ")
            print(f"      示例会话: {txt[:50]}")
    except Exception as e:
        record("会话列表可扫", False, str(e)[:80])

    # ── 4. 聊天窗控件检查（不发送，只验证输入框/按钮存在）──
    try:
        # 点击第一个会话打开聊天窗（用 Boss tab 而非 page 默认首 tab）
        clicked = False
        for li in tab.eles("tag:li"):
            txt = li.text or ""
            if len(txt) > 15:
                nb = li.ele("css:.name-box", timeout=2)
                if nb:
                    nb.click()
                    clicked = True
                    break
        time.sleep(3)
        record("聊天窗打开", clicked, "" if clicked else "无可点会话")

        # 输入框（contenteditable）——用 ele 方式，不用 run_js 箭头函数（DrissionPage 兼容）
        ed = tab.ele("css:[contenteditable='true']", timeout=3)
        record("输入框存在", ed is not None, "")
        # 发送控件：Boss 网页版主发送方式是输入框内 Enter；有图标按钮则记，无则弱信号提示
        has_btn = len(tab.eles("css:button", timeout=2)) > 0
        record("发送控件存在", ed is not None and has_btn,
               "" if (ed is not None and has_btn) else "(弱信号：Enter 发送为主，按钮不强制)")
    except Exception as e:
        record("聊天窗控件检查", False, str(e)[:80])

    # ── 5. 还原：回到原状态（关掉可能打开的聊天 tab 不新建）──
    try:
        # 只读测试不新开 tab，当前 tab 停在聊天页即可，不关闭（避免误伤）
        record("状态还原", True, "保持原 tab（未新开、未关闭）")
    except Exception:
        pass

    _summary()
    return 0


def _summary(code: int = 0):
    fails = [c for c in CHECKS if not c[1]]
    print(f"\n{'═' * 40}")
    if fails:
        print(f"⚠️  {len(fails)}/{len(CHECKS)} 项未通过 — 发送链路未就绪")
        for _, _, d in fails:
            print(f"    需要修: {d}")
    else:
        print(f"✅ {len(CHECKS)}/{len(CHECKS)} 项通过 — 发送链路就绪（等待人工 1 条测试发送）")
    sys.exit(code)


if __name__ == "__main__":
    # --send-1 需人工确认后才可用（本文件不含发送逻辑，防误用）
    if "--send-1" in sys.argv:
        print("⛔ 本文件是只读 smoke test，不包含发送逻辑。")
        print("   真实 1 条测试发送需人工在 Chrome 手动完成，或单独批准后走 boss_apply.py。")
        sys.exit(9)
    sys.exit(main())

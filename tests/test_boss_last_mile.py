#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Boss 最后一公里回归（2026-09-20）。

【为什么有这个文件】
Boss 近 40 天 uncertain 174 / failed 35、applied ≈ 1 —— 不是规则在拦人（拦人会记
skipped），是**点击之后系统认不出自己发成功了**。理由高度集中：

    79  「已找到会话但发送未验证」
    76  「会话已打开但发送未验证」
    60  「聊天页未找到会话(可能已用默认招呼语)」

用本机专用投递浏览器（9223 端口）做了**只读** DOM 探针，确诊两条、并修掉两条潜在隐患：

  ✅ 确诊一（对应那 76 条）：搜索页上根本没有 contenteditable，旧代码的兜底是
     「取第一个可见 textarea」。真机 zhipin.com/web/geek/jobs 上有 3 个可见 textarea，
     全是**岗位卡片下方的「请填写更多反馈意见…」反馈框**（祖先链 c-satisfaction-feedback，
     附近没有任何发送按钮）。于是招呼语被打进了岗位反馈框 → 没有发送按钮 → 落到 Enter
     键分支 → 什么都没发出去 → 验证时同一个框里还留着我们的文字 → has_text。
     这既是那 76 条的来源，也是**合规风险**（往平台反馈框里灌内容）。

  ✅ 确诊二（对应那 60 条）：真机 chat 页 `document.scrollHeight == innerHeight`，
     **window 自身不可滚**，旧代码 `window.scrollTo(...)` 等于什么都没做；真正的会话
     列表在 `.user-list-content`（可视 1073 / 内容 7997 / 40 个会话项）。列表没滚开，
     刚沟通的 HR 会话就没渲染出来 → not_found。

  ⚠️ 潜在隐患（真机 chat 页上未复现，但仍是错的，已一并修掉）：
     一、填充与验证**各写了一套选择器**：填充时过滤行内 display:none、验证时直接取
         `querySelector('[contenteditable="true"]')` 第一个。真机 chat 页上两者恰好都
         指向 DIV.chat-input（那个隐藏的残留框是 TEXTAREA，不匹配 contenteditable），
         所以没酿成事故 —— 但只要页面上多出一个隐藏的 contenteditable 就会验错元素。
     二、用 `offsetParent !== null` 判可见：**position:fixed 的元素 offsetParent 恒为
         null**，fixed 容器里的输入框会被当成隐藏元素跳过。

  ❌ 未确诊：那 79 条「已找到会话但发送未验证」在 chat 页上**新旧选择器指向同一个正确
     元素**，本文件的任何一条用例都解释不了它。要定论只能做一次真机单条发送对照，
     已单独上报，不在本文件的覆盖范围内 —— 不假装覆盖了。

【怎么测的】
不测 Python 那层（`tests/test_p0.py` 的 FakeTab 已经把「SENT_CLICKED/cleared/has_text
→ True/False」这层协议钉死了），也不复刻一份 JS 来测 —— 那样改了生产代码测试照样绿。
这里把 `boss_apply._JS_CHAT_INPUT` / `_JS_SCROLL_CHAT_LIST` 这两个**生产字符串本身**
取出来，喂给 node + 一个最小 DOM 桩执行 —— 测的就是线上真跑的那段代码。

每条动态用例都带**对照组**（忠实复刻旧行为，断言它确实会重现故障），所以用例不会
因为「桩造不出故障场景」而变成永远绿的摆设。

node 不在时 TestChatInputResolver 会 skip（Python 里没有 JS 引擎，这没法绕），
但结构组 TestSendPathStructure 永远会跑，它钉住的是「填充与验证必须共用同一个
定位函数、且这条路径上不许再出现 offsetParent」这个不变量，不依赖 node。
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import boss_apply as BA  # noqa: E402

_NODE = shutil.which("node")


# ── node 侧：最小 DOM 桩 + 用例 ──────────────────────────────────────────
_DOM_STUB = r"""
// ───── 最小 DOM 桩：只实现生产 JS 用到的那几个 API ─────
function El(tag, opts) {
    opts = opts || {};
    this.tagName = String(tag).toUpperCase();
    this.className = opts.cls || '';
    this.attrs = opts.attrs || {};
    this.textContent = opts.text || '';
    this.children = [];
    this.parentElement = null;
    this._rect = opts.hidden ? { width: 0, height: 0 } : (opts.rect || { width: 200, height: 40 });
    this._display = opts.hidden ? 'none' : (opts.display || 'block');
    this._visibility = opts.visibility || 'visible';
    this.scrollHeight = opts.scrollHeight || 0;
    this.clientHeight = opts.clientHeight || 0;
    this.scrollTop = 0;
    // offsetParent：模拟 fixed 定位 / 隐藏元素 → null（这正是缺陷二的触发条件）
    this.offsetParent = (opts.fixed || opts.hidden) ? null : { tagName: 'BODY' };
}
El.prototype.getBoundingClientRect = function () { return this._rect; };
El.prototype.appendChild = function (c) { c.parentElement = this; this.children.push(c); return c; };
El.prototype.querySelectorAll = function (sel) { return qsa(sel, this); };

function _matchOne(el, sel) {
    sel = sel.trim();
    if (sel === 'body') return el.tagName === 'BODY';
    if (sel.charAt(0) === '.') {
        var c = sel.slice(1);
        return el.className.split(/\s+/).indexOf(c) > -1;
    }
    var m;
    if ((m = sel.match(/^\[class\*="([^"]+)"\]$/))) return el.className.indexOf(m[1]) > -1;
    if ((m = sel.match(/^\[([\w-]+)="([^"]*)"\]$/))) return el.attrs[m[1]] === m[2];
    return el.tagName === sel.toUpperCase();
}
function _walk(node, out) {
    for (var i = 0; i < node.children.length; i++) { out.push(node.children[i]); _walk(node.children[i], out); }
    return out;
}
function qsa(sel, root) {
    // 注意：真浏览器里 document.querySelectorAll('body') 会返回 body 自身，
    // 所以候选集要把 root 一起算进去（否则生产 JS 里 scopes 的 'body' 兜底组永远落空）。
    var all = [root].concat(_walk(root, []));
    var groups = sel.split(',');
    return all.filter(function (el) {
        for (var g = 0; g < groups.length; g++) if (_matchOne(el, groups[g])) return true;
        return false;
    });
}

var window = {
    scrollTo: function () { window._scrollToCalls = (window._scrollToCalls || 0) + 1; },
    getComputedStyle: function (el) { return { display: el._display, visibility: el._visibility }; }
};
var document = {
    body: null,
    querySelectorAll: function (sel) { return qsa(sel, document.body); },
    querySelector: function (sel) { return qsa(sel, document.body)[0] || null; }
};
function freshBody() {
    document.body = new El('body');
    window._scrollToCalls = 0;
    return document.body;
}
freshBody();   // 生产片段末尾会立刻调一次 _jhScrollList()，先给个空 body
"""

_CASES = r"""
var R = {};

// ── 用例 1：先有一个隐藏的 contenteditable，真正的聊天输入框在后面 ──
// 直接复现缺陷一：旧验证代码 querySelector 取第一个 → 取到隐藏的那个。
(function () {
    var body = freshBody();
    var hiddenResidual = new El('div', { attrs: { contenteditable: 'true' }, hidden: true });
    body.appendChild(hiddenResidual);
    var wrap = new El('div', { cls: 'chat-container' });
    var realInput = new El('div', { cls: 'chat-input', attrs: { contenteditable: 'true' } });
    wrap.appendChild(realInput);
    body.appendChild(wrap);

    var picked = _jhChatInput();
    R.case1_picked = picked === realInput ? 'visible'
                  : (picked === hiddenResidual ? 'hidden' : 'none');
    // 对照组：证明这个桩确实能复现旧行为
    R.case1_old_pick = document.querySelector('[contenteditable="true"]') === hiddenResidual
                     ? 'hidden' : 'other';
})();

// ── 用例 2：position:fixed 的输入框（offsetParent === null）必须算可见 ──
(function () {
    var body = freshBody();
    var wrap = new El('div', { cls: 'chat-container' });
    var fixedEd = new El('div', { attrs: { contenteditable: 'true' }, fixed: true });
    wrap.appendChild(fixedEd);
    body.appendChild(wrap);
    R.case2_offsetParent_is_null = fixedEd.offsetParent === null;
    R.case2_picked = _jhChatInput() === fixedEd ? 'picked' : 'missed';
})();

// ── 用例 3：搜索框（祖先类名含 search）不能被当成聊天输入框 ──
(function () {
    var body = freshBody();
    var searchWrap = new El('div', { cls: 'chat-search' });
    var searchBox = new El('div', { attrs: { contenteditable: 'true' } });
    searchWrap.appendChild(searchBox);
    body.appendChild(searchWrap);
    var wrap = new El('div', { cls: 'chat-container' });
    var realInput = new El('div', { attrs: { contenteditable: 'true' } });
    wrap.appendChild(realInput);
    body.appendChild(wrap);
    var picked = _jhChatInput();
    R.case3_picked = picked === realInput ? 'skipped_search'
                  : (picked === searchBox ? 'picked_search' : 'none');
})();

// ── 用例 6：岗位卡片下方的「请填写更多反馈意见…」文本框必须被拒绝 ──
// 真机实测：zhipin.com/web/geek/jobs 上有 3 个可见 textarea，全是这个反馈框
// （祖先链 c-satisfaction-feedback，附近没有任何发送按钮）。旧代码的兜底是
// 「取第一个可见 textarea」→ 招呼语被打进反馈框 → 发不出去 → 那条 76 例的 UNCERTAIN。
(function () {
    var body = freshBody();
    var card = new El('div', { cls: 'recommend-result-job' });
    var fb = new El('textarea', { cls: 'input', attrs: { placeholder: '请填写更多反馈意见…' } });
    card.appendChild(fb);
    body.appendChild(card);

    var picked = _jhChatInput();
    R.case6_picked = picked === null ? 'rejected' : (picked === fb ? 'picked_feedback' : 'other');
    // 对照组：忠实复刻旧兜底（第一个 offsetParent 非 null 的 textarea）取到谁
    var oldPick = null, tas = document.querySelectorAll('textarea');
    for (var i = 0; i < tas.length; i++) {
        if (tas[i].offsetParent !== null) { oldPick = tas[i]; break; }
    }
    R.case6_old_pick = oldPick === fb ? 'feedback' : 'other';
})();

// ── 用例 7：没挂 chat-container 类名、但旁边就是发送按钮的内联聊天框，必须认出来 ──
// （防「矫枉过正」：收紧之后不能把真正的聊天框一起拒掉）
(function () {
    var body = freshBody();
    var panel = new El('div', { cls: 'dialog-wrap' });
    var ed = new El('div', { attrs: { contenteditable: 'true' } });
    var btn = new El('button', { cls: 'btn-send', text: '发送' });
    panel.appendChild(ed);
    panel.appendChild(btn);
    body.appendChild(panel);
    R.case7_picked = _jhChatInput() === ed ? 'picked_inline' : 'missed';
})();

// ── 用例 4：会话列表在内部滚动容器里，必须滚那个容器 ──
(function () {
    var body = freshBody();
    var inner = new El('div', { cls: 'chat-list-scroll', scrollHeight: 5000, clientHeight: 600 });
    var li = new El('li');
    li.appendChild(new El('span', { cls: 'name-box' }));
    inner.appendChild(li);
    body.appendChild(inner);
    _jhScrollList();
    R.case4_container_scrollTop = inner.scrollTop;
    R.case4_window_scroll_calls = window._scrollToCalls;
})();

// ── 用例 5：没有溢出的祖先容器不许被瞎设 scrollTop ──
(function () {
    var body = freshBody();
    var flat = new El('div', { cls: 'chat-list-plain', scrollHeight: 600, clientHeight: 600 });
    var li = new El('li');
    li.appendChild(new El('span', { cls: 'name-box' }));
    flat.appendChild(li);
    body.appendChild(flat);
    _jhScrollList();
    R.case5_flat_scrollTop = flat.scrollTop;
})();

console.log(JSON.stringify(R));
"""


def _run_node_cases() -> dict:
    """把生产 JS 字符串喂给 node + DOM 桩，返回各用例观测值。"""
    src = (_DOM_STUB
           + "\n// ───── 生产代码（直接取自 boss_apply，不是复刻）─────\n"
           + BA._JS_CHAT_INPUT
           + BA._JS_SCROLL_CHAT_LIST
           + "\n// ───── 用例 ─────\n"
           + _CASES)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "probe.js"
        f.write_text(src, encoding="utf-8")
        p = subprocess.run([_NODE, str(f)], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(f"node 执行失败：\n{p.stderr[:2000]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


class _CapturingTab:
    """记录 run_js 收到的 JS 原文，回放预设返回值（与 tests/test_p0.py 的 FakeTab 同构）。"""

    url = ""

    def __init__(self, results):
        self._results = list(results)
        self.scripts = []

    def run_js(self, js, *a, **k):
        self.scripts.append(js)
        return self._results.pop(0) if self._results else None

    def get(self, url):
        self.url = url

    def close(self):
        pass


class TestSendPathStructure(unittest.TestCase):
    """结构：钉住「填充与验证共用同一套定位」这个不变量。不依赖 node，永远会跑。"""

    JH_CATEGORY = "boss_last_mile"
    JH_NEW = True

    def _scripts(self):
        tab = _CapturingTab(["SENT_CLICKED", "cleared"])
        with mock.patch("boss_apply.time.sleep"):
            BA._fill_and_send(tab, "你好")
        return tab.scripts

    def test_fill_and_verify_use_the_same_resolver(self):
        scripts = self._scripts()
        self.assertEqual(len(scripts), 2, "填发一次应当只跑两次 JS（填充 + 验证）")
        for i, js in enumerate(scripts):
            self.assertIn("_jhChatInput()", js,
                          f"第 {i + 1} 次 run_js 没走统一入口 —— 填充/验证各写一套选择器就会验错元素")

    def test_send_path_no_longer_uses_offsetparent(self):
        """offsetParent 会把 position:fixed 的元素误判为隐藏，这段路径上不许再用。"""
        for i, js in enumerate(self._scripts()):
            self.assertNotIn("offsetParent", js,
                             f"第 {i + 1} 次 run_js 仍在用 offsetParent 判可见性")

    def test_chat_signal_uses_same_resolver(self):
        tab = _CapturingTab(["input"])
        self.assertEqual(BA._chat_signal(tab), "input")
        js = tab.scripts[0]
        self.assertIn("_jhChatInput", js)
        self.assertNotIn("offsetParent", js)

    def test_scroll_helper_scrolls_inner_container(self):
        """只 window.scrollTo 是旧行为；内部列表容器必须也滚。"""
        self.assertIn("scrollTop", BA._JS_SCROLL_CHAT_LIST)
        self.assertIn("_jhScrollList", BA._JS_SCROLL_CHAT_LIST)
        self.assertNotIn("offsetParent", BA._JS_SCROLL_CHAT_LIST)

    def test_resolver_has_no_hardcoded_display_none_filter(self):
        """旧填充逻辑靠 `[style*="display: none"]` 过滤，只认行内样式，靠不住。"""
        self.assertNotIn('style*="display: none"', BA._JS_CHAT_INPUT)
        self.assertIn("getBoundingClientRect", BA._JS_CHAT_INPUT)


@unittest.skipUnless(_NODE, "需要 node 才能执行生产 JS 片段（Python 里没有 JS 引擎）")
class TestChatInputResolver(unittest.TestCase):
    """动态：用真实 JS 引擎跑生产 `_JS_CHAT_INPUT` / `_JS_SCROLL_CHAT_LIST`。"""

    JH_CATEGORY = "boss_last_mile"
    JH_NEW = True

    @classmethod
    def setUpClass(cls):
        cls.r = _run_node_cases()

    def test_visible_input_wins_over_hidden_one(self):
        """页面先出现隐藏的 contenteditable 时，必须选到可见的聊天输入框。"""
        if self.r["case1_old_pick"] != "hidden":
            self.fail("DOM 桩没能复现旧的错误行为，这条用例就失去意义了")
        self.assertEqual(self.r["case1_picked"], "visible",
                         "取到了隐藏元素 → 会填/验到看不见的框上（「发送未验证」的来源）")

    def test_fixed_position_input_counts_as_visible(self):
        """position:fixed 的输入框 offsetParent 为 null，但它是可见的，不能跳过。"""
        self.assertTrue(self.r["case2_offsetParent_is_null"],
                        "桩没造出 offsetParent===null 的 fixed 元素，用例失效")
        self.assertEqual(self.r["case2_picked"], "picked",
                         "fixed 输入框被当成隐藏元素跳过 → 招呼语写进看不见的框")

    def test_search_box_is_not_the_chat_input(self):
        """检索框不是聊天输入框，不能被选中（否则招呼语会打进搜索框）。"""
        self.assertEqual(self.r["case3_picked"], "skipped_search")

    def test_job_card_feedback_box_is_rejected(self):
        """岗位卡片的「反馈意见」文本框必须被拒 —— 招呼语绝不能打进那里。

        真机 zhipin.com/web/geek/jobs 上有 3 个可见 textarea，全是这个反馈框。
        旧代码的兜底「取第一个可见 textarea」会正好选中它（对照组已证实）。
        """
        if self.r["case6_old_pick"] != "feedback":
            self.fail("DOM 桩没能复现旧的兜底行为，这条用例就失去意义了")
        self.assertEqual(self.r["case6_picked"], "rejected",
                         "选到了岗位反馈框 → 招呼语打进反馈框，发不出去还污染平台侧数据")

    def test_inline_chat_with_nearby_send_button_is_accepted(self):
        """收紧之后不能矫枉过正：没挂 chat 类名但旁边有发送按钮的内联聊天框仍要认。"""
        self.assertEqual(self.r["case7_picked"], "picked_inline")

    def test_scroll_targets_inner_container(self):
        """会话列表在内部滚动容器里，必须滚那个容器才算真的加载。"""
        self.assertEqual(self.r["case4_container_scrollTop"], 5000,
                         "内部列表没滚到底 → 刚沟通的 HR 会话加载不出来 → not_found")
        self.assertGreaterEqual(self.r["case4_window_scroll_calls"], 1)

    def test_no_scroll_on_container_without_overflow(self):
        """没有溢出的祖先容器不许被设 scrollTop（避免瞎滚页面）。"""
        self.assertEqual(self.r["case5_flat_scrollTop"], 0)


class TestSendVerdictTable(unittest.TestCase):
    """契约：验证返回值的判定表（与 tests/test_p0.py 同源，这里补上带诊断的返回格式）。"""

    JH_CATEGORY = "boss_last_mile"
    JH_NEW = True

    def _verdict(self, results):
        tab = _CapturingTab(results)
        with mock.patch("boss_apply.time.sleep"):
            return BA._fill_and_send(tab, "你好")

    def test_cleared_is_sent(self):
        self.assertIs(self._verdict(["SENT_CLICKED", "cleared"]), True)

    def test_residual_text_is_not_sent(self):
        self.assertIs(self._verdict(["SENT_CLICKED", "has_text"]), False)

    def test_residual_text_with_diagnostics_is_not_sent(self):
        """新格式带残留字数与标签：`has_text:37:DIV` 仍必须判否。"""
        self.assertIs(self._verdict(["SENT_CLICKED", "has_text:37:DIV"]), False)

    def test_no_input_is_still_treated_as_sent(self):
        """输入框消失 = 会话关闭。沿用既有语义（tests/test_p0.py 钉死过，不在此改动）。"""
        self.assertIs(self._verdict(["ENTER_KEY", "no_input"]), True)

    def test_none_js_result_is_not_sent(self):
        self.assertIs(self._verdict(["SENT_CLICKED", None]), False)

    def test_no_input_marker_is_not_sent(self):
        self.assertIs(self._verdict(["NO_INPUT"]), False)


class TestExistingLogWording(unittest.TestCase):
    """既有日志文案不能改：tests/test_p0.py 断言过这些子串。"""

    JH_CATEGORY = "boss_last_mile"
    JH_NEW = True

    def test_not_found_note_substring_preserved(self):
        src = Path(BA.__file__).read_text(encoding="utf-8")
        for s in ("聊天页未找到会话", "已找到会话但发送未验证", "会话已打开但发送未验证"):
            self.assertIn(s, src)

    def test_no_pii_in_new_js(self):
        """新加的 JS 里不许出现真实手机号/邮箱（PII 红线）。"""
        for blob in (BA._JS_CHAT_INPUT, BA._JS_SCROLL_CHAT_LIST):
            self.assertIsNone(re.search(r"1[3-9]\d{9}", blob))
            self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", blob))


if __name__ == "__main__":
    unittest.main()

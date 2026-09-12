#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5.3 51job 韧性测试（2026-09-12）：tab 掉线自愈 + 单小时熔断。

背景（两个真实事故）：
1. tab 掉线：本轮投到第 100 条时 tab 断连（"The connection to the page has been
   disconnected"），此后每个关键词都在 tab.get() 处抛异常，剩余 6 个关键词全部
   空转作废——Boss 侧有 _pick_boss_tab 兜底，51job 之前完全没有。
2. 小时闸缺失：config.safety.hourly_cap=10，但 51job 只认日限额，当日 08 时投 57 条、
   09 时投 38 条，超标 3-4 倍。
"""
import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path

import platform_51job as p

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'ab_experiment.db'


# ── tab 桩件 ──
class _LiveTab:
    """活着的 tab：run_js 正常返回。"""
    url = "about:blank"

    def __init__(self, name="live"):
        self.name = name
        self.closed = False

    def run_js(self, *_a, **_k):
        return 1

    def close(self):
        self.closed = True


class _DeadTab:
    """死掉的 tab：run_js 抛与生产一致的断连异常。"""
    url = "about:blank"

    def __init__(self):
        self.closed = False

    def run_js(self, *_a, **_k):
        raise RuntimeError("The connection to the page has been disconnected.")

    def close(self):
        self.closed = True


class _FakePage:
    """new_tab 按队列发牌；队列空则抛异常。"""

    def __init__(self, tabs):
        self._tabs = list(tabs)
        self.calls = 0

    def new_tab(self, _url):
        self.calls += 1
        if not self._tabs:
            raise RuntimeError("cannot create tab")
        return self._tabs.pop(0)


class TestTabGuard(unittest.TestCase):
    """tab 健康检查与自动重建。"""

    def setUp(self):
        p.TAB_READY_WAIT = 0
        p.TAB_RETRY_WAIT = 0

    # ── 正例：活着就不动（不能无谓重建，重建会丢页面状态）──
    def test_alive_tab_reused(self):
        t = _LiveTab()
        page = _FakePage([])
        got = p.ensure_tab(page, t)
        self.assertIs(got, t)
        self.assertEqual(page.calls, 0, "活 tab 不应触发重建")

    # ── 正例：死了就重建，且返回新 tab ──
    def test_dead_tab_rebuilt(self):
        dead = _DeadTab()
        fresh = _LiveTab("fresh")
        page = _FakePage([fresh])
        got = p.ensure_tab(page, dead)
        self.assertIs(got, fresh)
        self.assertTrue(dead.closed, "旧 tab 应被关闭")

    # ── 边界：tab 为 None 也要能重建（首次取 tab 的场景）──
    def test_none_tab_rebuilt(self):
        fresh = _LiveTab()
        page = _FakePage([fresh])
        self.assertIs(p.ensure_tab(page, None), fresh)

    # ── 反例：连续拿到的都是死 tab → 返回 None（调用方必须中止，不许空转）──
    def test_rebuild_failure_returns_none(self):
        page = _FakePage([_DeadTab(), _DeadTab(), _DeadTab()])
        self.assertIsNone(p.ensure_tab(page, _DeadTab()))
        self.assertEqual(page.calls, 3, "应重试 3 次后放弃")

    # ── 反例：new_tab 直接抛异常 → 也不能崩，返回 None ──
    def test_new_tab_raises_returns_none(self):
        page = _FakePage([])
        self.assertIsNone(p.ensure_tab(page, _DeadTab()))

    # ── _tab_alive 本身 ──
    def test_tab_alive_matrix(self):
        self.assertFalse(p._tab_alive(None))
        self.assertFalse(p._tab_alive(_DeadTab()))
        self.assertTrue(p._tab_alive(_LiveTab()))


class TestHourlyGate(unittest.TestCase):
    """单小时熔断：本小时已投 ≥ cap → 休息后继续。"""

    def setUp(self):
        p.HOURLY_REST_SEC = 0

    @staticmethod
    def _sql_count(hour_prefix):
        con = sqlite3.connect(str(DB))
        n = con.execute(
            "SELECT COUNT(*) FROM applications_v2 WHERE platform='51job' "
            "AND substr(created_at,1,13)=? AND status IN ('UNCERTAIN','APPLIED','VERIFIED')",
            (hour_prefix,)).fetchone()[0]
        con.close()
        return n or 0

    # ── 正例：与直接 SQL 完全对齐（今天 08 点真实投出过 57 条）──
    def test_matches_direct_sql_today(self):
        today = datetime.now().strftime('%Y-%m-%d')
        for hh in ('08', '09', '10'):
            prefix = f'{today}T{hh}'
            self.assertEqual(p.hourly_applied(prefix), self._sql_count(prefix),
                             f'{prefix} 统计应与 SQL 一致')

    # ── 关键回归：created_at 是 ISO 的 T 分隔，绝不能写成空格 ──
    #    首版 bug：用 "%Y-%m-%d %H" 生成前缀 → 与 DB 永不匹配 → 恒返回 0 →
    #    小时闸形同虚设。这条测试专门锁死这个坑。
    def test_hour_prefix_uses_iso_T_separator(self):
        today = datetime.now().strftime('%Y-%m-%d')
        space_prefix = f'{today} 08'
        self.assertNotIn(' ', datetime.now().strftime('%Y-%m-%dT%H'),
                         '前缀必须是 T 分隔')
        # 空格前缀必然查不到（证明两种写法不等价，防止有人改回空格）
        self.assertEqual(len(space_prefix), 13)
        if self._sql_count(f'{today}T08') > 0:
            self.assertEqual(p.hourly_applied(space_prefix), 0,
                             '空格前缀不应匹配到任何记录')

    # ── 反例：不含任何记录的整点 → 0（不串其它小时的数据）──
    def test_unrelated_hour_is_zero(self):
        self.assertEqual(p.hourly_applied('2099-01-01T00'), 0)

    # ── 边界：cap=0 视为不启用，立即返回 ──
    def test_gate_disabled_when_cap_zero(self):
        p.hourly_gate(0)   # 不应抛异常、不应 sleep

    # ── 边界：未达上限 → 不休息 ──
    def test_gate_not_triggered_below_cap(self):
        p.hourly_gate(10 ** 9)

    # ── 正例：达上限 → 触发（休息时长置 0，能立刻走完）──
    def test_gate_triggers_at_cap(self):
        import unittest.mock as mock
        with mock.patch.object(p, 'hourly_applied', return_value=57):
            p.hourly_gate(10)   # 57 ≥ 10 → 走触发分支；rest=0 → 立即返回


class TestSafetyConfig(unittest.TestCase):
    """限额/护栏配置防漂移。"""

    def test_daily_limit_is_100(self):
        # 2026-09-12 用户定稿：以当日实际投出量 100 作为 51job 日限额
        self.assertEqual(p.DAILY_LIMIT, 100)

    def test_load_safety_matches_config_json(self):
        with open(ROOT / 'config.json', encoding='utf-8') as f:
            cfg = json.load(f)
        want = (cfg.get('safety') or {}).get('hourly_cap')
        if want is not None:
            self.assertEqual(p.load_safety().get('hourly_cap'), want,
                             'load_safety 必须与 config.json 的 safety.hourly_cap 一致')

    def test_load_safety_has_required_keys(self):
        s = p.load_safety()
        for k in ('hourly_cap', 'night_ban_start', 'night_ban_end'):
            self.assertIn(k, s)


if __name__ == '__main__':
    unittest.main()

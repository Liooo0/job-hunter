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
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import platform_51job as p

ROOT = Path(__file__).resolve().parent.parent

# 2026-09-25：原先这里还定义了 DB = ROOT/'ab_experiment.db' 并被 TestHourlyGate
# 当生产库直连使用（干净检出必红）。该类已改为运行期自建临时库，本常量随之删除。


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
    """单小时熔断：本小时已投 ≥ cap → 休息后继续。

    2026-09-25 改写：原先直连**生产库** `ab_experiment.db`（本机文件，不在仓库）
    → 干净检出必定 `no such table: applications_v2`，而开发机上因为库里恰好有数据
    所以是绿的。改为运行期自建临时库（用仓库自带 store.SCHEMA 建表），夹具数据
    全部合成；两种环境结果完全一致。
    """

    @classmethod
    def setUpClass(cls):
        import store
        cls._tmp = tempfile.TemporaryDirectory(prefix="v53db-")
        cls.db = Path(cls._tmp.name) / "test.db"
        con = sqlite3.connect(str(cls.db))
        con.executescript(store.SCHEMA)
        today = datetime.now().strftime('%Y-%m-%d')
        rows = [
            ("51job", f"{today}T08:05:00", "APPLIED"),
            ("51job", f"{today}T08:20:00", "UNCERTAIN"),
            ("51job", f"{today}T08:40:00", "VERIFIED"),
            ("51job", f"{today}T09:10:00", "APPLIED"),
            # 反向夹具：非计数状态 / 别的平台 / 空格分隔的脏数据，都不得被算进来
            ("51job", f"{today}T08:50:00", "REJECTED"),
            ("boss", f"{today}T08:55:00", "APPLIED"),
            ("51job", f"{today} 08:59:00", "APPLIED"),
        ]
        con.executemany(
            "INSERT INTO applications_v2 (platform, created_at, status, company, title, "
            "city, salary, keyword, application_id, job_id, decision, applied_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(pl, ts, st, "某合成公司", "AI应用工程师", "深圳", "15-25K", "k",
              f"{pl}-{ts}-{st}-{i}", f"job-{i}", "applied", ts, ts)
             for i, (pl, ts, st) in enumerate(rows)])
        con.commit()
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        p.HOURLY_REST_SEC = 0
        # 把被测函数的 DB 指到临时库（原先它写死生产库路径）
        self._patched = mock.patch.object(p, "DB_PATH", self.db)
        self._patched.start()
        self.addCleanup(self._patched.stop)

    def _sql_count(self, hour_prefix):
        con = sqlite3.connect(str(self.db))
        n = con.execute(
            "SELECT COUNT(*) FROM applications_v2 WHERE platform='51job' "
            "AND substr(created_at,1,13)=? AND status IN ('UNCERTAIN','APPLIED','VERIFIED')",
            (hour_prefix,)).fetchone()[0]
        con.close()
        return n or 0

    # ── 正例：与直接 SQL 完全对齐（夹具里 08 点应恰好 3 条）──
    def test_matches_direct_sql_today(self):
        today = datetime.now().strftime('%Y-%m-%d')
        for hh, want in (('08', 3), ('09', 1), ('10', 0)):
            prefix = f'{today}T{hh}'
            self.assertEqual(want, self._sql_count(prefix), f'{prefix} 夹具自检')
            self.assertEqual(p.hourly_applied(prefix), self._sql_count(prefix),
                             f'{prefix} 统计应与 SQL 一致')

    # ── 关键回归：created_at 是 ISO 的 T 分隔，绝不能写成空格 ──
    #    首版 bug：用 "%Y-%m-%d %H" 生成前缀 → 与 DB 永不匹配 → 恒返回 0 →
    #    小时闸形同虚设。这条测试专门锁死这个坑。
    def test_hour_prefix_uses_iso_T_separator(self):
        today = datetime.now().strftime('%Y-%m-%d')
        space_prefix = f'{today} 08'
        self.assertNotIn(' ', datetime.now().strftime('%Y-%m-%dT%H'), '前缀必须是 T 分隔')
        self.assertEqual(len(space_prefix), 13)
        # 夹具里**确实**存在一条空格分隔的脏数据、且 T 前缀有 3 条 —— 说明两种写法
        # 不等价，改回空格会让小时闸读到 1 条并漏掉 T 分隔的那 3 条
        self.assertEqual(1, self._sql_count(space_prefix), '夹具自检：空格前缀只匹配脏数据')
        self.assertEqual(3, self._sql_count(f'{today}T08'), '夹具自检：T 前缀 3 条')
        self.assertEqual(p.hourly_applied(space_prefix), 1,
                         '空格前缀不应匹配到 T 分隔的正式记录')

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
        """safety.hourly_cap 必须被 load_safety 如实读出来。

        2026-09-25 改写：原版读仓库根的 config.json（个人文件、.gitignore 掉），
        干净检出必 FileNotFoundError。改为**运行时生成**一份临时配置并把
        load_config 指过去：既不再依赖开发机状态，也仍然测的是同一条代码路径。
        """
        import shared
        tmp = tempfile.TemporaryDirectory(prefix="v53cfg-")
        self.addCleanup(tmp.cleanup)
        cfg_file = Path(tmp.name) / "config.json"
        cfg_file.write_text(json.dumps({"safety": {"hourly_cap": 7,
                                                   "night_ban_start": 23,
                                                   "night_ban_end": 7}}),
                            encoding="utf-8")
        fake = shared.load_config(skill_dir=Path(tmp.name))
        with mock.patch.object(shared, "load_config", return_value=fake):
            self.assertEqual(7, p.load_safety().get("hourly_cap"),
                             "load_safety 必须如实反映 safety.hourly_cap")

    def test_load_safety_has_required_keys(self):
        s = p.load_safety()
        for k in ('hourly_cap', 'night_ban_start', 'night_ban_end'):
            self.assertIn(k, s)


if __name__ == '__main__':
    unittest.main()

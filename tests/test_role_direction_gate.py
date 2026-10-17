#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""岗位方向闸回归（2026-09-23）。

起因（用户当天发现）：A 池用「游戏内容运营」在 51job 搜索，51job 是**模糊匹配**，
返回的却是泛营销/媒介岗 —— 决策层原来不看「岗位方向」，于是这 7 条被当成游戏线投了：

    12:29 小红书媒介专员                深圳市华谊酒业贸易有限公司  8千-1.2万
    12:29 小红书内容运营                安莉芳（中国）服装有限公司  9千-1.5万
    12:29 新媒体运营主管（小红书+抖音）  深圳市华谊酒业贸易有限公司  1.2-1.5万
    12:29 新媒体运营（工作室）           深圳广播电影电视集团        1-1.1万
    12:27 海外 KOL 运营专员（英语市场）  深圳市八位堂科技有限公司    1.2-1.8万·13薪
    12:27 海外 KOL 运营专员（非英语市场）深圳市八位堂科技有限公司    1.2-1.8万·13薪

用户原话：「这不符合我们的筛选条件啊。」

跑法：env PYTHONPATH="" /usr/bin/python3 -m unittest tests.test_role_direction_gate -v
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 注意：**不要**在这里 setdefault("JH_LINE", ...)。
# 2026-09-23 实测踩过：本文件设了 JH_LINE=transition 后，同进程里
# tests/test_job_decision.py 的 <5K 用例（4-6K，期望 REJECT）因为过渡线
# 「≥4K 放行」而变成 ALLOW，全量跑挂 4 例 —— 单跑却全绿，典型的测试污染。
# 本文件的用例全部显式传 line="transition"，不依赖环境变量。

from job_decision import evaluate_job, title_direction_block, round_direction  # noqa: E402


class TestTitleDirectionGate(unittest.TestCase):

    def test_today_51job_media_jobs_blocked_in_game_round(self):
        """今天真实投出去的那一批：方向=game 时全部必须被拦。"""
        rows = [
            ("深圳市华谊酒业贸易有限公司", "小红书媒介专员", "8千-1.2万"),
            ("安莉芳（中国）服装有限公司", "小红书内容运营", "9千-1.5万"),
            ("深圳市华谊酒业贸易有限公司", "新媒体运营主管（小红书+抖音方向，需0-1建店经验）", "1.2-1.5万"),
            ("深圳广播电影电视集团", "新媒体运营（工作室）(J10466)", "1-1.1万"),
            ("深圳市八位堂科技有限公司", "海外 KOL 运营专员（英语市场）", "1.2-1.8万·13薪"),
            ("深圳市八位堂科技有限公司", "海外 KOL 运营专员（非英语市场）", "1.2-1.8万·13薪"),
        ]
        for company, title, salary in rows:
            with self.subTest(title=title):
                d = evaluate_job(company, title, "", salary, line="transition", direction="game")
                self.assertEqual(d.action, "REJECT", f"{title} 应被拦，实际 {d}")
                self.assertIn("泛营销/媒介岗", d.reason)

    def test_plain_ops_job_blocked_in_game_round(self):
        """没有营销词、但也不是游戏岗 → 由方向闸拦（A池只找游戏行业岗）。"""
        d = evaluate_job("某某科技有限公司", "运营专员", "", "9千-1.3万",
                         line="transition", direction="game")
        self.assertEqual(d.action, "REJECT")
        self.assertIn("A池方向闸", d.reason)

    def test_real_game_jobs_pass(self):
        """真游戏岗必须放行（不能把 A 池本身打死）。"""
        rows = [
            ("某游戏公司", "游戏运营助理", "9千-1.3万"),
            ("某网络科技", "游戏社区运营", "8千-1.2万"),
            ("某科技", "玩家运营", "1-1.5万"),
            ("某科技", "游戏数据运营", "9千-1.4万"),
            ("某互动娱乐", "海外游戏运营（英语）", "1.2-1.8万"),
        ]
        for company, title, salary in rows:
            with self.subTest(title=title):
                d = evaluate_job(company, title, "", salary, line="transition", direction="game")
                self.assertEqual(d.action, "ALLOW", f"{title} 不该被拦：{d.reason}")

    def test_game_anchor_exempts_media_word(self):
        """「游戏新媒体运营」是游戏行业岗位 → 营销词豁免，不误杀。"""
        self.assertIsNone(title_direction_block("游戏新媒体运营", "game"))
        d = evaluate_job("某游戏公司", "游戏新媒体运营", "", "9千-1.2万",
                         line="transition", direction="game")
        self.assertEqual(d.action, "ALLOW")

    def test_media_gate_applies_to_non_game_rounds_too(self):
        """非游戏轮次（AI线/B池）同样不投营销媒介岗 —— 用户口径是「不是目标方向」。"""
        for title in ("小红书媒介专员", "新媒体运营（AI视频内容方向）",
                      "海外 KOL 运营专员", "短视频运营", "私域运营专员"):
            with self.subTest(title=title):
                d = evaluate_job("某某公司", title, "", "1-1.5万", line="transition")
                self.assertEqual(d.action, "REJECT", f"{title} 应被拦：{d}")
                self.assertIn("泛营销/媒介岗", d.reason)

    def test_b_pool_normal_jobs_unaffected(self):
        """B 池（现金流线）正岗不能被误伤。"""
        rows = [
            ("某某公司", "运营助理", "4.5-6千"),
            ("某某公司", "文员", "4-5千"),
            ("某某公司", "行政助理", "5-7千"),
            ("某某公司", "内容审核", "5-7千"),
            ("某某公司", "门店内务", "4.5-5.5千"),
        ]
        for company, title, salary in rows:
            with self.subTest(title=title):
                d = evaluate_job(company, title, "", salary, line="transition")
                self.assertEqual(d.action, "ALLOW", f"{title} 不该被拦：{d.reason}")

    def test_ai_line_untouched(self):
        """AI 主线正岗照旧放行。"""
        rows = [
            ("慧博云通科技股份有限公司", "AI应用工程师", "1.2-2万"),
            ("某科技", "AI实施工程师", "9千-1.5万"),
            ("某科技", "大模型应用工程师", "2-3万"),
        ]
        for company, title, salary in rows:
            with self.subTest(title=title):
                d = evaluate_job(company, title, "", salary, line="ai", direction=None)
                self.assertEqual(d.action, "ALLOW", f"{title} 不该被拦：{d.reason}")

    def test_case_insensitive_media_tokens(self):
        """KOL/MCN/SEO 这些英文词大小写混写也要命中。"""
        for title in ("海外kol运营", "MCN内容运营", "SEO优化专员", "Kol达人运营"):
            with self.subTest(title=title):
                self.assertIsNotNone(title_direction_block(title, "game"))


class TestRoundDirection(unittest.TestCase):
    """整轮判方向：A 池关键词里混着「活动运营」这种泛词，逐词判会漏。"""

    def test_game_round_detected_from_any_keyword(self):
        self.assertEqual(round_direction(["游戏运营", "活动运营", "游戏内容运营"]), "game")
        self.assertEqual(round_direction(["玩家运营"]), "game")
        self.assertEqual(round_direction(["活动运营", "游戏运营助理"]), "game")

    def test_non_game_rounds(self):
        self.assertIsNone(round_direction(["AI应用工程师", "AI实施", "RPA开发"]))
        self.assertIsNone(round_direction(["运营助理", "文员", "内容审核"]))
        self.assertIsNone(round_direction([]))
        self.assertIsNone(round_direction(None))

    def test_generic_keyword_in_game_round_still_gated(self):
        """即使命中来自「活动运营」这个泛词，整轮是游戏向 → 泛运营岗照样拦。"""
        rows = [
            ("某科技", "市场运营经理", "2-2.5万·13薪"),
            ("某科技", "reddit社区运营", "1.2-2万"),
            ("某科技", "B端行业社群运营(A38069)", "1-1.5万·14薪"),
        ]
        d_round = round_direction(["游戏运营", "活动运营"])
        for company, title, salary in rows:
            with self.subTest(title=title):
                d = evaluate_job(company, title, "", salary, line="transition", direction=d_round)
                self.assertEqual(d.action, "REJECT", f"{title} 应被拦：{d.reason}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
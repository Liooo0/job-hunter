"""公司级黑名单回归用例（2026-09-20）

背景（真实事故，不是假想）：
  COMPANY_REDLINES 只按"公司名里是否含红线词"判定 —— 而外包/人服/狼性销售类公司
  的注册名一个红线词都没有，于是漏网：
    法本  103 条投递（2026-06-17 → 09-20，51job 上判 APPLIED，真投了）
    珍岛  162 条投递（2026-06-23 → 09-19）
  本用例锁定"公司级黑名单"这一层：命中即 REJECT，一票否决，对所有 line 生效。

⚠️ 关键保护用例：慧博云通 不得被黑名单误伤 —— 该公司 HR 正在与用户正常沟通。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import job_decision as jd  # noqa: E402


def _action(d):
    """兼容 Decision 的动作字段命名。"""
    for name in ("decision", "action", "verdict", "result"):
        if hasattr(d, name):
            return str(getattr(d, name)).upper()
    return None


def _reason(d):
    return str(getattr(d, "reason", "") or "")


def _decide(company, title="AI 应用工程师", jd_text="", salary="1-1.5万", line=None):
    if line is None:
        return jd.evaluate_job(company, title, jd_text, salary)
    return jd.evaluate_job(company, title, jd_text, salary, line=line)


class TestCompanyBlacklist(unittest.TestCase):
    """用户点名的两家 + 同类主体，一律 REJECT。"""

    def test_fabben_51job_raw_name_rejected(self):
        """51job 的原始公司名带后缀和换行，黑名单必须仍能命中。"""
        name = "深圳市法本信息技术股份有限公司\n计算机软件已上市"
        d = _decide(name, title="游戏舆情用户运营", salary="7千-1万")
        self.assertEqual(_action(d), "REJECT", f"法本未被拦：{d}")
        self.assertIn("黑名单", _reason(d))

    def test_fabben_short_name_rejected(self):
        d = _decide("法本", title="文本大模型评测", salary="1-1.5万")
        self.assertEqual(_action(d), "REJECT")
        self.assertIn("黑名单", _reason(d))

    def test_zhendao_group_rejected(self):
        d = _decide("珍岛集团", title="AI Agent邀约专员", salary="8千-1.3万")
        self.assertEqual(_action(d), "REJECT", f"珍岛未被拦：{d}")
        self.assertIn("黑名单", _reason(d))

    def test_outsourcing_peers_rejected(self):
        """同一红线（人服/IT外包/销售型SaaS）的典型主体。"""
        for name in ["外企德科数字技术有限公司", "中软国际", "软通动力信息技术",
                     "博彦科技", "文思海辉", "中电金信", "人瑞人才", "探迹"]:
            with self.subTest(company=name):
                d = _decide(name, title="AI 应用工程师", salary="1-1.5万")
                self.assertEqual(_action(d), "REJECT", f"{name} 未被拦：{d}")

    def test_blacklist_applies_to_transition_line_too(self):
        """过渡线同样不得放行（黑名单与 line 无关）。"""
        d = _decide("法本", title="运营助理", salary="5-6千", line="transition")
        self.assertEqual(_action(d), "REJECT", "过渡线上黑名单失效")
        self.assertIn("黑名单", _reason(d))


class TestBlacklistDoesNotOverreach(unittest.TestCase):
    """反向保护：不得误伤。"""

    def test_huiboyuntong_not_blocked(self):
        """⚠️ 慧博云通 HR 正在正常沟通 —— 绝不能被黑名单误杀。"""
        d = _decide("慧博云通科技股份有限公司", title="音视频大模型专家", salary="1.5-2.5万")
        self.assertNotIn("黑名单", _reason(d), f"误伤了正在沟通的慧博云通：{d}")

    def test_normal_big_company_not_blocked(self):
        for name in ["腾讯科技（深圳）有限公司", "字节跳动", "华为技术有限公司"]:
            with self.subTest(company=name):
                d = _decide(name, title="AI 应用工程师", salary="1.5-2.5万")
                self.assertNotIn("黑名单", _reason(d), f"误伤 {name}：{d}")

    def test_name_containing_blacklist_substring_only_in_title(self):
        """黑名单只作用于公司名，不作用于岗位标题。"""
        d = _decide("某某科技有限公司", title="法本信息驻场岗位", salary="1-1.5万")
        self.assertNotIn("黑名单", _reason(d), "黑名单误扫了标题")


class TestExistingRedlinesStillWork(unittest.TestCase):
    """回归：原有主体红线不得被这次改动破坏。"""

    def test_hr_company_still_rejected_by_redline(self):
        d = _decide("某某人力资源服务有限公司", title="AI 应用工程师", salary="1-1.5万")
        self.assertEqual(_action(d), "REJECT")
        self.assertIn("主体红线", _reason(d))

    def test_blacklist_constants_are_disjoint_from_redlines(self):
        self.assertTrue(set(jd.COMPANY_BLACKLIST), "黑名单为空")
        self.assertIn("法本", jd.COMPANY_BLACKLIST)
        self.assertIn("珍岛", jd.COMPANY_BLACKLIST)


if __name__ == "__main__":
    unittest.main()

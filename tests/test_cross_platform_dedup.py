#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨平台公司去重测试（P0-2 回归）：验证 51job 与猎聘的跨批次公司去重逻辑。"""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import store
from store import company_applied_recently, record_application, normalize_company


class TestCompanyDedup(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_dedup.db"
        self.patcher = mock.patch.object(store, "DB", self.db_path)
        self.patcher.start()
        # 初始化数据库表结构
        conn = store._conn()
        conn.close()

    def tearDown(self):
        self.patcher.stop()
        self.tmp_dir.cleanup()

    def _insert_app(self, platform, city, company, days_ago, decision="ALLOW", status="APPLIED"):
        applied_at = (datetime.now() - timedelta(days=days_ago)).isoformat()
        record_application(
            platform=platform,
            city=city,
            company=company,
            title="测试岗位",
            salary="15-25K",
            keyword="AI工程师",
            score=80,
            resume_version="E",
            decision=decision,
            status=status,
            reason="测试记录",
            verified=1,
            event_type="apply",
            extra_payload={"applied_at": applied_at},
        )
        # 确保 applied_at 为指定的时间戳
        conn = store._conn()
        cn = normalize_company(company)
        conn.execute(
            "UPDATE applications_v2 SET applied_at = ? WHERE platform = ? AND company_norm = ?",
            (applied_at, platform, cn),
        )
        conn.commit()
        conn.close()

    def test_case_1_company_applied_3_days_ago_is_skipped(self):
        """Case 1: 同公司 3 天前已投 -> 当前岗位必须判定为已投（跳过）。"""
        self._insert_app("51job", "深圳", "腾讯科技有限公司", days_ago=3)
        self.assertTrue(
            company_applied_recently("深圳", "腾讯科技有限公司", days=7, platform="51job"),
            "3天前已投的公司在7天窗口内必须被判定为已投",
        )
        self._insert_app("liepin", "深圳", "阿里巴巴集团", days_ago=3)
        self.assertTrue(
            company_applied_recently("深圳", "阿里巴巴集团", days=7, platform="liepin"),
            "猎聘平台3天前已投的公司在7天窗口内必须被判定为已投",
        )

    def test_case_2_company_applied_8_days_ago_is_allowed(self):
        """Case 2: 同公司 8 天前已投 -> 超出窗口，允许继续。"""
        self._insert_app("51job", "深圳", "百度在线网络技术有限公司", days_ago=8)
        self.assertFalse(
            company_applied_recently("深圳", "百度在线网络技术有限公司", days=7, platform="51job"),
            "8天前投递的公司超出7天窗口，不应被去重拦截",
        )
        self._insert_app("liepin", "深圳", "百度在线网络技术有限公司", days_ago=8)
        self.assertFalse(
            company_applied_recently("深圳", "百度在线网络技术有限公司", days=7, platform="liepin"),
            "猎聘平台8天前投递超出窗口，不应被拦截",
        )

    def test_case_3_different_company_is_unaffected(self):
        """Case 3: 不同公司 -> 不受影响。"""
        self._insert_app("51job", "深圳", "字节跳动", days_ago=1)
        self.assertFalse(
            company_applied_recently("深圳", "快手科技", days=7, platform="51job"),
            "不同公司绝不能被去重误伤",
        )
        self.assertFalse(
            company_applied_recently("深圳", "美团", days=7, platform="liepin"),
            "未投过的公司绝不能被拦截",
        )

    def test_case_4_same_company_multiple_jobs_in_same_round(self):
        """Case 4: 同公司两个岗位、同一轮抓取 -> 第一个投递后，第二个必须被去重拦截。"""
        company = "商汤科技"
        city = "深圳"
        # 初始状态：未投递
        self.assertFalse(company_applied_recently(city, company, days=7, platform="51job"))
        # 岗位 1 成功投递入库
        self._insert_app("51job", city, company, days_ago=0)
        # 岗位 2 处理时，立即能查出同公司已投
        self.assertTrue(
            company_applied_recently(city, company, days=7, platform="51job"),
            "同公司首个岗位投递后，同批次后续岗位必须被去重拦截",
        )

    def test_case_5_company_name_normalization_variations(self):
        """Case 5: company 名称存在大小写/空格/常见后缀差异 -> 必须由 normalize_company 统一识别。"""
        self._insert_app("51job", "深圳", "Tencent Technology Co., Ltd.", days_ago=1)
        # 测试大小写与前后空格变体
        self.assertTrue(
            company_applied_recently("深圳", "  tencent technology co., ltd.  ", days=7, platform="51job"),
            "公司名大小写和空格差异应被规范化正确匹配",
        )
        self.assertTrue(
            company_applied_recently("深圳", "TENCENT TECHNOLOGY CO., LTD.", days=7, platform="51job"),
            "全大写变体应被正确匹配",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

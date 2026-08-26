#!/usr/bin/env python3
"""v2.1 任务一：决策链快照（Decision Trace）单元测试。

覆盖：
- trace 构建与 gate/finalize/to_json 基本语义；
- 短路验收样例：被 smart_filter 杀掉的岗位，下游门全部保持 not_reached；
- store.py 幂等迁移（gates / greeting_template_id 列）+ 落库往返。

跑法：
    PYTHONPATH="" PYTHONPYCACHEPREFIX=/tmp/jh_pyc /usr/bin/python3 -m unittest \
        discover -s tests -p "test_*.py" -t .
或直接运行本文件。
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import decision_trace  # noqa: E402
import store  # noqa: E402


class TestTraceBasics(unittest.TestCase):
    def test_new_trace_prefills_all_gates_not_reached(self):
        tr = decision_trace.new_trace()
        self.assertEqual(set(tr["gates"].keys()), set(decision_trace.GATES))
        for g in tr["gates"].values():
            self.assertEqual(g["result"], "not_reached")
            self.assertEqual(g["detail"], "")
        self.assertEqual(tr["final_decision"], "")
        self.assertEqual(tr["final_reason"], "")

    def test_gate_pass_and_rejected(self):
        tr = decision_trace.new_trace()
        decision_trace.gate(tr, "smart_filter", "pass")
        decision_trace.gate(tr, "deep_filter", "rejected:实习薪资陷阱", detail="薪资<100")
        self.assertEqual(tr["gates"]["smart_filter"]["result"], "pass")
        self.assertEqual(tr["gates"]["deep_filter"]["result"], "rejected:实习薪资陷阱")
        self.assertEqual(tr["gates"]["deep_filter"]["detail"], "薪资<100")

    def test_gate_none_trace_is_noop(self):
        decision_trace.gate(None, "smart_filter", "pass")  # 不应抛异常

    def test_finalize_and_to_json_roundtrip(self):
        tr = decision_trace.new_trace()
        decision_trace.finalize(tr, "skipped", "低于最低分")
        data = json.loads(decision_trace.to_json(tr))
        self.assertEqual(data["final_decision"], "skipped")
        self.assertEqual(data["final_reason"], "低于最低分")

    def test_to_json_none_for_empty_trace(self):
        self.assertIsNone(decision_trace.to_json(None))
        self.assertIsNone(decision_trace.to_json({}))


class TestShortCircuitAcceptance(unittest.TestCase):
    """任务书验收样例：smart_filter 杀掉 → 下游门只标 not_reached。"""

    def _simulate_smart_filter_kill(self):
        """模拟 _prepare_job_context 的短路时序（不触发任何网络/页面操作）。"""
        tr = decision_trace.new_trace()
        # dedup 在循环体先过
        decision_trace.gate(tr, "dedup", "pass")
        # smart_filter 杀掉 → 记录 rejected 后直接 return "skip"
        decision_trace.gate(tr, "smart_filter", "rejected:公司规模不满足",
                            detail="50人以下")
        return tr  # 下游门一个都不碰

    def test_downstream_gates_stay_not_reached(self):
        tr = self._simulate_smart_filter_kill()
        self.assertEqual(tr["gates"]["smart_filter"]["result"],
                         "rejected:公司规模不满足")
        for name in ("deep_filter", "company_profile", "min_score",
                     "already_chatted", "apply"):
            self.assertEqual(tr["gates"][name]["result"], "not_reached",
                             f"{name} 不应被触达")


class TestStoreMigrationAndRoundtrip(unittest.TestCase):
    """store.py v2.1 列：幂等迁移 + record_application 往返。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db = store.DB
        self._orig_ready = store._v21_columns_ready
        store.DB = Path(self._tmp.name) / "test_v21.db"
        store._v21_columns_ready = False  # 每个用例重新走迁移路径

    def tearDown(self):
        store.DB = self._orig_db
        store._v21_columns_ready = self._orig_ready
        self._tmp.cleanup()

    def test_fresh_db_has_v21_columns_and_migration_is_idempotent(self):
        conn = store._conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(applications_v2)")}
        self.assertIn("gates", cols)
        self.assertIn("greeting_template_id", cols)
        # 重复执行迁移无副作用
        store._ensure_v21_columns(conn)
        store._ensure_v21_columns(conn)
        conn.close()

    def test_legacy_db_alter_adds_columns(self):
        # 手工造一个"旧版"库：无 gates / greeting_template_id 列
        conn = sqlite3.connect(store.DB)
        conn.executescript("""
            CREATE TABLE applications_v2 (
                application_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                company_norm TEXT, decision TEXT NOT NULL, status TEXT NOT NULL,
                applied_at TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL);
            CREATE TABLE jobs (job_id TEXT PRIMARY KEY, company_norm TEXT);
            CREATE TABLE events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT, job_id TEXT, type TEXT,
                timestamp TEXT NOT NULL, payload TEXT, error TEXT);
        """)
        conn.commit()
        conn.close()
        store._v21_columns_ready = False  # 强制重走 ALTER 迁移
        conn = store._conn()  # 触发幂等补列
        cols = {r[1] for r in conn.execute("PRAGMA table_info(applications_v2)")}
        self.assertIn("gates", cols)
        self.assertIn("greeting_template_id", cols)
        conn.close()

    def test_record_application_persists_gates_and_template_id(self):
        tr = decision_trace.new_trace()
        decision_trace.gate(tr, "smart_filter", "rejected:X")
        aid = store.record_application(
            platform="boss", city="深圳", company="测试公司", title="AI应用工程师",
            salary="20-30K", keyword="AI应用", score=75, resume_version="D-AI应用",
            decision="skipped", status="SKIPPED", reason="X",
            gates=decision_trace.to_json(tr), greeting_template_id="T1:AI应用",
        )
        conn = store._conn()
        row = conn.execute(
            "SELECT gates, greeting_template_id FROM applications_v2 WHERE application_id=?",
            (aid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["greeting_template_id"], "T1:AI应用")
        gates = json.loads(row["gates"])
        self.assertEqual(gates["gates"]["smart_filter"]["result"], "rejected:X")
        self.assertEqual(gates["gates"]["deep_filter"]["result"], "not_reached")
        # events payload 也带 gates JSON
        ev = conn.execute(
            "SELECT payload FROM events WHERE application_id=? ORDER BY event_id DESC LIMIT 1",
            (aid,)).fetchone()
        payload = json.loads(ev["payload"])
        self.assertIn("gates", payload)  # payload 里是 gates JSON 字符串
        gates_in_payload = json.loads(payload["gates"])
        self.assertEqual(gates_in_payload["final_decision"], "")  # 未 finalize 时为空串
        conn.close()

    def test_old_rows_read_back_null_gates(self):
        aid = store.record_application(
            platform="boss", city="深圳", company="老数据公司", title="测试工程师",
            salary="15-25K", keyword="测试", score=60, resume_version="B-测试",
            decision="applied", status="APPLIED", reason="ok",
            # 不传 gates / greeting_template_id → 旧行为完全一致，列为 NULL
        )
        conn = store._conn()
        row = conn.execute(
            "SELECT gates, greeting_template_id FROM applications_v2 WHERE application_id=?",
            (aid,)).fetchone()
        self.assertIsNone(row["gates"])
        self.assertIsNone(row["greeting_template_id"])
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

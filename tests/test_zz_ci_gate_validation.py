"""一次性实验：验证 CI 的测试门槛真的会红（验完即删分支，不合并）。"""
import unittest


class CiGateValidation(unittest.TestCase):
    def test_deliberately_failing(self):
        self.assertEqual(1, 2, "这是故意失败的用例：CI 必须因此变红")

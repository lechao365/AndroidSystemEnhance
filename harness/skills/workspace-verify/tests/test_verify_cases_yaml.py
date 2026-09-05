# verify-cases.yaml 资产契约断言：trigger 首项 root 等待须条件化
# （adb root 输出含 already 即 adbd 未重启，无条件 sleep 2 纯浪费——
# acc_13/25 恒 2.55s 的构成，A4 消除）。断言锚点用 "already"/"grep -q"
# 子串（无条件版两者皆无），规避 payload 内转义引号的切分歧义。

import unittest
from pathlib import Path

import yaml

_YAML = (Path(__file__).resolve().parents[3] / "config"
         / "verify-cases.yaml")


class TestTriggerFirstRootWait(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))

    def _acceptance(self, case):
        text = self.data["cases"][case]["acceptance"]
        self.assertTrue(text.startswith("hostcmd:"),
                        f"{case} 首项应为 hostcmd")
        return text

    def test_lcview_trigger_conditional_wait(self):
        text = self._acceptance("lcview-trigger")
        self.assertIn("grep -q", text)
        self.assertIn("already", text)  # 无条件版两者皆无（回归锚点）
        self.assertIn("sleep 2", text)  # 条件分支内保留（真实切换仍须等待）

    def test_lciod_trigger_conditional_wait(self):
        text = self._acceptance("lciod-trigger")
        self.assertIn("grep -q", text)
        self.assertIn("already", text)

    def test_yaml_loads_and_cases_intact(self):
        self.assertIn("lcview-liveness", self.data["cases"])
        self.assertIn("lciod-liveness", self.data["cases"])
        self.assertTrue(self.data.get("modules"))

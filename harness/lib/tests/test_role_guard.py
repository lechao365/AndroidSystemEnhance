"""role_guard 单测：角色读取优先级与门禁拦截行为。

环境变量注入角色（任务约定：测试不依赖本机 paths.conf 实际配置），
conf 读取路径经 _conf_role seam 隔离。
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import role_guard  # noqa: E402


class TestGetRole(unittest.TestCase):
    def test_default_is_apply(self):
        # 安全缺省：未配置（环境变量与 conf 均无）→ apply 设备
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HARNESS_ROLE", None)
            with mock.patch.object(role_guard, "_conf_role",
                                   return_value=""):
                self.assertEqual(role_guard.get_role(), "apply")

    def test_conf_value_used_when_env_absent(self):
        # paths.conf 配置值生效（emit 设备显式配置）
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HARNESS_ROLE", None)
            with mock.patch.object(role_guard, "_conf_role",
                                   return_value="emit"):
                self.assertEqual(role_guard.get_role(), "emit")

    def test_env_overrides_conf(self):
        # 环境变量 HARNESS_ROLE 覆盖 conf（apply/emit 双向均覆盖）
        with mock.patch.dict(os.environ, {"HARNESS_ROLE": "apply"}):
            with mock.patch.object(role_guard, "_conf_role",
                                   return_value="emit"):
                self.assertEqual(role_guard.get_role(), "apply")
        with mock.patch.dict(os.environ, {"HARNESS_ROLE": "emit"}):
            with mock.patch.object(role_guard, "_conf_role",
                                   return_value="apply"):
                self.assertEqual(role_guard.get_role(), "emit")


class TestRequireRole(unittest.TestCase):
    def test_match_returns_current_role(self):
        with mock.patch.dict(os.environ, {"HARNESS_ROLE": "emit"}):
            self.assertEqual(role_guard.require_role("emit"), "emit")

    def test_mismatch_exits_1_with_role_mismatch_message(self):
        # 不匹配 → SystemExit(1)，stderr 含 ROLE_MISMATCH 分类字样与
        # 当前/期望角色
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"HARNESS_ROLE": "apply"}):
            with redirect_stderr(buf):
                with self.assertRaises(SystemExit) as cm:
                    role_guard.require_role("emit")
        self.assertEqual(cm.exception.code, 1)
        err = buf.getvalue()
        self.assertIn("ROLE_MISMATCH", err)
        self.assertIn("apply", err)
        self.assertIn("emit", err)


if __name__ == "__main__":
    unittest.main()

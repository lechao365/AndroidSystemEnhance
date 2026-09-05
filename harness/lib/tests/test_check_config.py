# check_config.py 单测（方向 1/3）：verify-cases.yaml 治理校验、paths.conf
# 已知键与 baseline-status 同名一致性、command/skill 契约（按实际集合遍历，
# 不固化数量）。ROOT 经 CHECK_CONFIG_ROOT 注入临时目录隔离。

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_config.py"

# 合法最小 fixture（数量不固化：config 1 模块 / contract 2 command 足以核对关系）
YAML_OK = (
    "modules:\n"
    "  m1:\n"
    "    targets: [t1, t2]\n"
    "    test_targets: [u1]\n"
    "    push:\n"
    "      - module: b1\n"
    "        dst: [/vendor/bin/b1]\n"
    "cases:\n"
    "  c1: 'svc:a'\n"
    "  c2:\n"
    "    acceptance: 'svc:b'\n"
    "    setup_snapshot: ['cat x']\n"
    "    teardown: ['echo 1 > x']\n"
    "    timeout_s: 60\n"
)

PATHS_OK = (
    "# 注释行\n"
    'PATCHS_DIR="code/rpi5"\n'
    'KERNEL_WS="${KERNEL_WS:-/home/u/ws/kernel}"\n'
    'AOSP_WS="${AOSP_WS:-/home/u/ws/aosp}"\n'
    'LC_VERIFY_EXPECT_SERIAL="${LC_VERIFY_EXPECT_SERIAL:-}"\n'
)

BASELINE_NO_PATHS = "baselines: []\n"


class CheckConfigTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ["CHECK_CONFIG_ROOT"] = str(self.root)
        cfg = self.root / "harness" / "config"
        cfg.mkdir(parents=True)
        (cfg / "verify-cases.yaml").write_text(YAML_OK, encoding="utf-8")
        (cfg / "paths.conf").write_text(PATHS_OK, encoding="utf-8")
        (cfg / "baseline-status.yaml").write_text(BASELINE_NO_PATHS,
                                                  encoding="utf-8")

    def tearDown(self):
        os.environ.pop("CHECK_CONFIG_ROOT", None)
        self._tmp.cleanup()

    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    def _rewrite_cases(self, text):
        (self.root / "harness" / "config" / "verify-cases.yaml").write_text(
            text, encoding="utf-8")


class TestConfigMode(CheckConfigTestBase):
    def test_valid_fixture_passes(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("OK: config 检查通过", r.stdout)

    def test_module_missing_key_is_red(self):
        self._rewrite_cases(YAML_OK.replace("    targets: [t1, t2]\n", ""))
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("modules.m1 缺 targets", r.stdout)

    def test_duplicate_targets_across_modules_is_red(self):
        # 方向 1：targets 跨模块唯一（不固化模块数——两模块即核对该关系）
        text = YAML_OK.replace(
            "cases:\n",
            "  m2:\n"
            "    targets: [t1]\n"
            "    test_targets: [u2]\n"
            "    push:\n"
            "      - module: b2\n"
            "        dst: [/system/bin/b2]\n"
            "cases:\n")
        self._rewrite_cases(text)
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("targets 重复", r.stdout)
        self.assertIn("t1", r.stdout)

    def test_relative_push_dst_is_red(self):
        self._rewrite_cases(YAML_OK.replace("dst: [/vendor/bin/b1]",
                                            "dst: [vendor/bin/b1]"))
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("push 目标须绝对路径", r.stdout)

    def test_case_dict_unknown_key_is_red(self):
        self._rewrite_cases(YAML_OK.replace("    timeout_s: 60\n",
                                            "    timeout_s: 60\n"
                                            "    extra_key: 1\n"))
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("cases.c2 含未知键: extra_key", r.stdout)

    def test_case_dict_missing_acceptance_is_red(self):
        self._rewrite_cases(YAML_OK.replace("    acceptance: 'svc:b'\n", ""))
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("cases.c2 缺 acceptance", r.stdout)

    def test_case_timeout_not_positive_int_is_red(self):
        for bad in ("0", "-5", "'60'", "true"):
            self._rewrite_cases(YAML_OK.replace("    timeout_s: 60",
                                                f"    timeout_s: {bad}"))
            r = self._run()
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("timeout_s 须为正整数", r.stdout, bad)

    def test_paths_unknown_and_missing_keys_are_red(self):
        cfg = self.root / "harness" / "config" / "paths.conf"
        cfg.write_text(PATHS_OK + 'EXTRA_KEY="/x"\n', encoding="utf-8")
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("paths.conf 未知键: EXTRA_KEY", r.stdout)
        cfg.write_text('PATCHS_DIR="code/rpi5"\n', encoding="utf-8")
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("paths.conf 缺已知键", r.stdout)

    def test_baseline_same_name_field_mismatch_is_red(self):
        # 方向 2：baseline-status.yaml 与 paths.conf 同名字段须一致
        baseline = ("baselines:\n"
                    "- baseline_id: BL-20260101-01\n"
                    "  aosp_ws: /some/other/path\n")
        (self.root / "harness" / "config" / "baseline-status.yaml").write_text(
            baseline, encoding="utf-8")
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("与 paths.conf AOSP_WS", r.stdout)
        self.assertIn("/some/other/path", r.stdout)

    def test_baseline_same_name_field_match_passes(self):
        baseline = ("baselines:\n"
                    "- baseline_id: BL-20260101-01\n"
                    "  aosp_ws: /home/u/ws/aosp\n")
        (self.root / "harness" / "config" / "baseline-status.yaml").write_text(
            baseline, encoding="utf-8")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_root_empty_env_falls_back_to_default(self):
        # 方向 5：CHECK_CONFIG_ROOT 空串时回落默认值（Path("") 解析为 "."
        # 会漂移检查根，须 strip 后判空）
        code = (
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('cc', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "default = Path(m.__file__).resolve().parents[2]\n"
            "sys.exit(0 if m.ROOT == default else 1)\n"
        )
        env = dict(os.environ)
        env["CHECK_CONFIG_ROOT"] = ""
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPT)],
                           capture_output=True, text=True,
                           encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestContractMode(CheckConfigTestBase):
    """方向 3：command/skill 契约按实际集合遍历（fixture 数量任意）。"""

    def setUp(self):
        super().setUp()
        self._mk_cmd = lambda n: (self.root / ".opencode" / "command").mkdir(
            parents=True, exist_ok=True) or \
            (self.root / ".opencode" / "command" / f"{n}.md").write_text(
                "doc\n", encoding="utf-8")
        self._mk_skill = lambda n: (self.root / "harness" / "skills" / n).mkdir(
            parents=True, exist_ok=True)

    def _mk_skill_with_md(self, n):
        self._mk_skill(n)
        (self.root / "harness" / "skills" / n / "SKILL.md").write_text(
            "x\n", encoding="utf-8")

    def test_no_commands_at_all_is_red(self):
        r = self._run("--contract")
        self.assertEqual(r.returncode, 1)
        self.assertIn("未发现 command 文件", r.stdout)

    def test_exempt_commands_pass_without_skill(self):
        # 豁免清单（opencode-server 内建 / cross-device-* SKILL 在 opencode 侧）
        # 不要求 harness 侧同名 skill；fixture 3 个 command 亦核对（不固化数量）
        for n in ("opencode-server", "cross-device-apply", "cross-device-emit"):
            self._mk_cmd(n)
        r = self._run("--contract")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_non_exempt_command_without_skill_is_red(self):
        self._mk_cmd("my-skill")
        r = self._run("--contract")
        self.assertEqual(r.returncode, 1)
        self.assertIn("command my-skill 无对应 skill", r.stdout)

    def test_command_with_skill_md_passes(self):
        self._mk_cmd("my-skill")
        self._mk_skill_with_md("my-skill")
        r = self._run("--contract")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_skill_missing_skill_md_is_red(self):
        self._mk_cmd("my-skill")
        self._mk_skill("my-skill")  # 有目录无 SKILL.md
        r = self._run("--contract")
        self.assertEqual(r.returncode, 1)
        self.assertIn("skill my-skill 缺 SKILL.md", r.stdout)

    def test_noise_dirs_not_treated_as_skills(self):
        # 缓存目录（.pytest_cache/__pycache__）不纳入契约面
        self._mk_cmd("a")
        self._mk_skill_with_md("a")
        self._mk_skill(".pytest_cache")
        self._mk_skill("__pycache__")
        r = self._run("--contract")
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()

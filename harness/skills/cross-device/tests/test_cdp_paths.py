import os
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_paths


class TestCdpPaths(unittest.TestCase):
    def setUp(self):
        # 全部用例走临时根，避免在真实仓库 mkdir data/verify-results 弄脏工作树
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old
        self._tmp.cleanup()

    def test_data_verify_results_dir_env_override(self):
        self.assertEqual(str(cdp_paths.data_verify_results_dir()),
                         os.path.join(self._tmp.name, "data", "verify-results"))

    def test_receipt_dir_mkdir(self):
        d = cdp_paths.data_verify_results_dir()
        self.assertTrue(d.is_dir())

    def test_log_apply_dir_env_override(self):
        self.assertEqual(str(cdp_paths.log_apply_dir()),
                         os.path.join(self._tmp.name, "harness", "log",
                                      "cross-device"))

    def test_log_apply_dir_mkdir(self):
        d = cdp_paths.log_apply_dir()
        self.assertTrue(d.is_dir())

    def test_data_dir_names_match_paths_constants(self):
        # R2 漂移守卫：cdp_paths 数据目录名须与 paths.py 单一事实源一致
        # （cdp_paths 受限环境回退本地字面量，此处显式载入 paths 校验不漂移）
        lib = Path(__file__).resolve().parents[2] / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        import paths
        self.assertEqual(cdp_paths.data_verify_results_dir().name,
                         paths.DATA_VERIFY_RESULTS_DIRNAME)
        self.assertEqual(cdp_paths.data_known_issues_dir().name,
                         paths.DATA_KNOWN_ISSUES_DIRNAME)

    @pytest.mark.real_repo("回落包目录探测脚本路径（只读真实仓）")
    def test_cdp_parse_script_path_resolution(self):
        # 未设 CDP_PROJECT_ROOT 时基于包目录探测（只读校验，不 mkdir）。
        # 注：cdp_parse.py 由 Task 1.1 创建，此处只校验路径解析（父目录即本模块所在目录）。
        os.environ.pop("CDP_PROJECT_ROOT")
        p = cdp_paths.cdp_parse_script()
        self.assertEqual(p.name, "cdp_parse.py")
        self.assertTrue(p.parent.is_dir(), f"父目录应存在: {p.parent}")


if __name__ == "__main__":
    unittest.main()
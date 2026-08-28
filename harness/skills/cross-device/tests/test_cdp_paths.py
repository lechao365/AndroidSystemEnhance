import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_paths


class TestCdpPaths(unittest.TestCase):
    def setUp(self):
        # 全部用例走临时根，避免在真实仓库 mkdir data/verify 弄脏工作树
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old
        self._tmp.cleanup()

    def test_data_verify_dir_env_override(self):
        self.assertEqual(str(cdp_paths.data_verify_dir()),
                         os.path.join(self._tmp.name, "data", "verify"))

    def test_receipt_dir_mkdir(self):
        d = cdp_paths.data_verify_dir()
        self.assertTrue(d.is_dir())

    def test_cdp_parse_script_path_resolution(self):
        # 未设 CDP_PROJECT_ROOT 时基于包目录探测（只读校验，不 mkdir）。
        # 注：cdp_parse.py 由 Task 1.1 创建，此处只校验路径解析（父目录即本模块所在目录）。
        os.environ.pop("CDP_PROJECT_ROOT")
        p = cdp_paths.cdp_parse_script()
        self.assertEqual(p.name, "cdp_parse.py")
        self.assertTrue(p.parent.is_dir(), f"父目录应存在: {p.parent}")


if __name__ == "__main__":
    unittest.main()
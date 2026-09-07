#!/usr/bin/env python3
"""check_hot_path_scan 单测：治理检查器热路径禁全树 rglob/os.walk。

用临时目录模拟仓（只读文件，无需 git）；清单文件缺失须报（覆盖断链）。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_hot_path_scan as chps  # noqa: E402


class TestHotPathScan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        # 按清单建最小仓，逐测试覆写目标文件
        for rel in chps._HOT_PATHS:
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("pass\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _put(self, rel: str, content: str):
        (self.repo / rel).write_text(content)

    def test_clean_ok(self):
        # 清单文件合规：零违规
        self.assertEqual(chps.scan(self.repo), [])

    def test_rglob_reported(self):
        # 热路径检查器新增全树 rglob → 违规
        self._put("harness/lib/check_config.py",
                  "def f():\n    for x in root.rglob('*'):\n        pass\n")
        out = chps.scan(self.repo)
        self.assertTrue(any("check_config.py" in o and "rglob" in o
                            for o in out))

    def test_os_walk_reported(self):
        self._put("harness/lib/check_config.py",
                  "def f():\n    for d in os.walk('.'):\n        pass\n")
        self.assertTrue(any("os.walk" in o for o in chps.scan(self.repo)))

    def test_gitls_fallback_exempt(self):
        # 明确标注 # GITLS-FALLBACK 的非 git 回落分支不判红
        self._put("harness/lib/check_config.py",
                  "def f():\n"
                  "    for x in base.rglob('*'):  # GITLS-FALLBACK: 非 git 回落\n"
                  "        pass\n")
        self.assertEqual(chps.scan(self.repo), [])

    def test_comment_line_ignored(self):
        # 纯注释行提及 rglob 不计
        self._put("harness/lib/check_config.py",
                  "# 注意：不要用 rglob('*') 全树遍历\npass\n")
        self.assertEqual(chps.scan(self.repo), [])

    def test_missing_listed_file_reported(self):
        # 清单文件缺失 = 守卫覆盖断链，报违规
        (self.repo / "harness/lib/check_skill_refs.py").unlink()
        out = chps.scan(self.repo)
        self.assertTrue(any("缺失" in o for o in out))


if __name__ == "__main__":
    unittest.main()

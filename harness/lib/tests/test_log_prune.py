"""log_prune 单元测试（批次五 D2）。

monkeypatch 模块级 REPO_ROOT 指向临时目录构造文件集，验证：
mtime 过龄清理 / 数量上限删最旧 / dry-run 不真删 / --apply 真删 /
glob 误配静默零删（不报错）。
"""
import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("log_prune", _LIB / "log_prune.py")
log_prune = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(log_prune)


class TestLogPrune(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._orig_root = log_prune.REPO_ROOT
        log_prune.REPO_ROOT = self.root

    def tearDown(self):
        log_prune.REPO_ROOT = self._orig_root
        self._tmp.cleanup()

    def _touch(self, rel, mtime, content="x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p

    def test_stale_files_removed_by_apply(self):
        old = time.time() - 40 * 86400
        self._touch("harness/log/git-works-push/git-works-push-20200101.log", old)
        fresh = self._touch("harness/log/git-works-push/git-works-push-new.log",
                            time.time())
        plan = log_prune.run(days=30, targets=[
            "harness/log/git-works-push/*.log"], apply=True)
        self.assertEqual(plan["total_removed"], 1)
        self.assertFalse((self.root / plan["patterns"][0]
                          ["removed"][0]["file"]).exists())
        self.assertTrue(fresh.exists())

    def test_over_limit_removes_oldest(self):
        now = time.time()
        paths = [self._touch(
            f"harness/log/promote-{i:03d}.head", now - (i + 1) * 86400)
            for i in range(4)]
        plan = log_prune.run(days=30, max_files=2,
                             targets=["harness/log/promote-*.head"],
                             apply=True)
        self.assertEqual(plan["total_removed"], 2)
        self.assertTrue(paths[0].exists())
        self.assertTrue(paths[1].exists())
        self.assertFalse(paths[2].exists())
        self.assertFalse(paths[3].exists())

    def test_dry_run_keeps_files(self):
        old = time.time() - 100 * 86400
        p = self._touch("harness/log/git-works-push/a.log", old)
        plan = log_prune.run(days=30, targets=[
            "harness/log/git-works-push/*.log"], apply=False)
        self.assertEqual(plan["total_removed"], 1)
        self.assertTrue(p.exists())

    def test_no_match_is_silent_zero(self):
        plan = log_prune.run(days=30, targets=["harness/log/nope/*.log"])
        self.assertEqual(plan["total_removed"], 0)
        self.assertEqual(plan["patterns"][0]["scanned"], 0)

    def test_days_zero_clears_all(self):
        now = time.time()
        p = self._touch("harness/log/git-works-push/b.log", now)
        log_prune.run(days=0, targets=["harness/log/git-works-push/*.log"],
                      apply=True)
        self.assertFalse(p.exists())


class TestRepoRootAnchor(unittest.TestCase):
    """方向 1：REPO_ROOT 锚点自证（不 patch 模块变量）。

    回归防线：此前 REPO_ROOT 误取 harness/（parents[0]）致 DEFAULT_TARGETS
    恒零命中仍返 0，靠 patch 的假目录用例掩盖。此处直接对真实模块断言：
    REPO_ROOT == 仓根，且三条默认 glob 各自能命中——产物由用例自建（方向 1：
    干净克隆无 gitignore 域产物，断言真实产物存在会恒零命中致 CI 红）。
    """
    # 自建/清理真实仓 gitignore 域临时产物（drvfs 下 glob ~3s），豁免 slow guard
    pytestmark = pytest.mark.slow_ok("真实仓默认目标锚点自证")

    def _probe_rel(self, pattern):
        """把默认 glob 首个 * 替换为测试唯一 token，得到可匹配的相对路径。"""
        token = f"prune-anchor-{os.getpid()}"
        return pattern.replace("*", token, 1)

    def test_repo_root_is_repo_root_without_patch(self):
        repo_root = Path(__file__).resolve().parents[3]
        self.assertEqual(log_prune.REPO_ROOT, repo_root)
        self.assertTrue((log_prune.REPO_ROOT / "harness").is_dir())

    def test_default_targets_hit_real_dir(self):
        # 自建临时产物再 glob 证锚点（不依赖真实仓 gitignore 产物）：对每条
        # 默认 glob 在其静态目录下创建匹配文件，验证 REPO_ROOT 锚点命中后清理
        created = []
        try:
            for pattern in log_prune.DEFAULT_TARGETS:
                p = log_prune.REPO_ROOT / self._probe_rel(pattern)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x", encoding="utf-8")
                created.append(p)
                hits = list(log_prune.REPO_ROOT.glob(pattern))
                self.assertTrue(
                    hits, f"默认目标 {pattern} 在真实仓零命中（错路径复发？）")
        finally:
            for p in created:
                p.unlink(missing_ok=True)

    def test_prune_real_dir_with_defaults(self):
        # 以真实仓运行默认目标 dry-run：不误删（apply=False）且不报错
        plan = log_prune.run(targets=log_prune.DEFAULT_TARGETS, apply=False)
        self.assertEqual(plan["errors"], [])


if __name__ == "__main__":
    unittest.main()

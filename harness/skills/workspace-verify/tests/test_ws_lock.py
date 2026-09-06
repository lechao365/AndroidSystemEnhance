# ws_lock 单测：workspace/device 双文件锁（fcntl.flock 非阻塞主路径 +
# O_EXCL 回退路径）。关键场景：加解锁往返、并发占用即拒（LockHeld）、
# verify_locks 成对加解与半持锁回收、无 fcntl 环境退化行为。

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_lock


class TestFileLock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / ".lock-x"

    def tearDown(self):
        self._tmp.cleanup()

    def test_acquire_release_roundtrip(self):
        lk = ws_lock.FileLock(self.path)
        self.assertTrue(lk.acquire())
        self.assertTrue(self.path.exists())
        lk.release()
        self.assertFalse(lk._fd is not None)

    def test_contention_raises_lock_held(self):
        # 第二把锁同路径占用即拒（不等待）：编排互斥核心语义
        lk1 = ws_lock.FileLock(self.path)
        lk1.acquire()
        try:
            with self.assertRaises(ws_lock.LockHeld):
                ws_lock.FileLock(self.path).acquire()
        finally:
            lk1.release()
        # 释放后可再取（flock 随 fd 关闭归还）
        lk2 = ws_lock.FileLock(self.path)
        lk2.acquire()
        lk2.release()

    def test_release_idempotent(self):
        lk = ws_lock.FileLock(self.path)
        lk.release()  # 未持锁释放：无害空操作
        lk.acquire()
        lk.release()
        lk.release()

    def test_context_manager(self):
        with ws_lock.FileLock(self.path):
            pass
        # 退出即释放：可立即再取
        with ws_lock.FileLock(self.path):
            pass

    def test_fallback_without_fcntl(self):
        # 无 fcntl 环境：O_EXCL 创建式锁，占用即拒，释放删文件
        lk1 = ws_lock.FileLock(self.path)
        lk2 = ws_lock.FileLock(self.path)
        with mock.patch.object(ws_lock, "fcntl", None):
            lk1.acquire()
            self.assertTrue(self.path.exists())
            with self.assertRaises(ws_lock.LockHeld):
                lk2.acquire()
            lk1.release()
            self.assertFalse(self.path.exists())  # 回退路径释放即删文件


class TestVerifyLocks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_acquires_both_locks_in_order(self):
        with ws_lock.verify_locks(self.dir) as locks:
            self.assertEqual(list(locks.keys()), list(ws_lock.LOCK_NAMES))
            for name in ws_lock.LOCK_NAMES:
                self.assertTrue((self.dir / f".lock-{name}").exists())

    def test_released_after_exit(self):
        with ws_lock.verify_locks(self.dir):
            pass
        # 成对释放：可立即整体再取
        with ws_lock.verify_locks(self.dir):
            pass

    def test_second_lock_held_releases_first(self):
        # device 锁被外部占用：workspace 锁先取得后回收（不留半持锁态）
        outside = ws_lock.FileLock(self.dir / ".lock-device")
        outside.acquire()
        try:
            with self.assertRaises(ws_lock.LockHeld):
                with ws_lock.verify_locks(self.dir):
                    pass
        finally:
            outside.release()
        # workspace 锁已被回收：可立即取得
        with ws_lock.verify_locks(self.dir):
            pass

    def test_exception_inside_still_releases(self):
        # 编排体异常：锁仍成对归还（finally 保证）
        with self.assertRaises(RuntimeError):
            with ws_lock.verify_locks(self.dir):
                raise RuntimeError("编排体失败")
        with ws_lock.verify_locks(self.dir):
            pass

    def test_default_lock_dir_is_log_workspace_verify(self):
        # 默认锁目录锚定 harness/log/workspace-verify（gitignore 域）
        self.assertEqual(ws_lock.DEFAULT_LOCK_DIR,
                         Path(ws_lock.__file__).resolve().parents[2]
                         / "log" / "workspace-verify")


if __name__ == "__main__":
    unittest.main()

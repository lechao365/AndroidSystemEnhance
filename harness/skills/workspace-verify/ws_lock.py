#!/usr/bin/env python3
# ============================================================
# ws_lock.py — workspace-verify 编排互斥双文件锁（workspace + device）
# 所属模块：workspace-verify — 编译产物上板验证
# 设计目的：链式编排（ws_verify_chain.py）进出时对 workspace 与 device
#   两类互斥资源加解锁，防止两条验证链并发踩踏（sync 写 workspace 与
#   push/acceptance 操作设备均不可重入）。锁为 fcntl.flock 非阻塞独占：
#   - 持锁进程死亡内核自动释放，无陈锁残留（优于 O_EXCL 轮转）
#   - 占用时立即失败（rc 3），等待/重试策略归调用方编排
#   - 非 POSIX 环境（无 fcntl）退化为 O_CREAT|O_EXCL 创建式锁，尽力而为
# 锁文件落 harness/log/workspace-verify/（整体 gitignore，不入库）。
# 仅编排器（ws_verify_chain.py）调用；子脚本不得自行加锁（同进程重入
# 第二把 fd 会非阻塞失败，属预期防护而非 bug）。
# ============================================================

import errno
import os
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # 非 POSIX（罕见）：退化为 O_EXCL 创建式锁
    fcntl = None

_SCRIPT_DIR = Path(__file__).resolve().parent
# 默认锁目录：harness/log/workspace-verify/（_SCRIPT_DIR=skills/workspace-verify）
DEFAULT_LOCK_DIR = _SCRIPT_DIR.parents[1] / "log" / "workspace-verify"

# 两把锁的语义化名称（编排进出成对加解锁，顺序固定防死锁）
LOCK_NAMES = ("workspace", "device")


class LockHeld(RuntimeError):
    """锁被其他编排进程占用（含锁文件创建失败/权限问题）。"""


class FileLock:
    """单文件非阻塞独占锁（flock 主路径 / O_EXCL 回退路径）。"""

    def __init__(self, path):
        self.path = Path(path)
        self._fd = None

    def acquire(self):
        """获取锁；占用即抛 LockHeld（不等待，重试策略归调用方）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl is not None:
            # O_RDWR 建文件（已存在不截断，避免抹掉持锁者痕迹）
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                raise LockHeld(f"锁被占用: {self.path} ({exc})") from exc
            # 写入持锁 pid 便于人工排查（flock 语义下仅提示性）
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode())
            except OSError:
                pass  # 提示信息写失败不影响锁语义
            self._fd = fd
            return True
        # 回退路径：O_EXCL 原子创建（进程死亡不自动释放，尽力而为）
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.EACCES, errno.EAGAIN):
                raise LockHeld(f"锁被占用: {self.path} ({exc})") from exc
            raise
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return True

    def release(self):
        """释放锁（幂等；未持锁调用为无害空操作）。"""
        if self._fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        except OSError:
            pass  # 释放失败不阻断编排退出（flock 随 fd 关闭自动释放）
        finally:
            self._fd = None
            if fcntl is None:
                try:
                    self.path.unlink()
                except OSError:
                    pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


@contextmanager
def verify_locks(lock_dir=None):
    """编排互斥上下文：按固定顺序（workspace→device）加两把锁，退出成对释放。

    任一锁占用即抛 LockHeld，且已取得的锁先释放（不留半持锁态）。
    用法：
        with verify_locks():
            ...  # 编排体
    """
    d = Path(lock_dir) if lock_dir else DEFAULT_LOCK_DIR
    locks = [FileLock(d / f".lock-{name}") for name in LOCK_NAMES]
    acquired = []
    try:
        for lk in locks:
            lk.acquire()  # 占用抛 LockHeld
            acquired.append(lk)
        yield {name: lk for name, lk in zip(LOCK_NAMES, locks)}
    finally:
        # 逆序释放，保证任何异常路径下锁都归还
        for lk in reversed(acquired):
            lk.release()

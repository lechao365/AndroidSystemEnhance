"""lib-13：原子写原语并发互写防护（cdp_paths.atomic_write_text /
verify_common.atomic_write_json）。

同 pid 多线程并发写同一路径：tmp 名带 pid + 线程 id，各线程内容互不
覆盖，读到/最终留下的均为某一次完整写入（无混合截断），结束后无残留
tmp 文件（旧实现 tmp 名仅含 pid，两线程同写同 tmp 互踩可产出损坏文件）。

导入纪律：本文件必须经 importlib 以别名加载被测模块，**禁止裸名
`import cdp_paths`**——全量收集时 harness/lib/tests 按字母序先于
cross-device 测试执行，裸 import 会把 harness/lib 版（无
cdp_parse_script）缓存进 sys.modules["cdp_paths"]，令
cross-device/tests/test_cdp_paths 的垫片专属断言 AttributeError
（lib-13 修复批引入过的回归）。
"""
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_LIB_DIR = Path(__file__).resolve().parents[1]


def _load_lib_module(alias: str, filename: str):
    """以独立别名显式加载 harness/lib 模块，不污染裸名 sys.modules 缓存。"""
    spec = importlib.util.spec_from_file_location(alias, _LIB_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cdp_paths = _load_lib_module("_cdp_paths_impl_under_test", "cdp_paths.py")
verify_common = _load_lib_module(
    "_verify_common_impl_under_test", "verify_common.py")


def _payload(tag: str) -> str:
    """自描述完整载荷：首尾哨兵成对出现且仅一次，混合/截断可检出。"""
    return f"HEAD-{tag}-" + ("x" * 512) + f"-TAIL-{tag}"


class TestBareNameImportHygiene(unittest.TestCase):
    def test_bare_cdp_paths_cache_keeps_shim_semantics(self):
        # 全套件中裸名 cdp_paths 若已被缓存，必须是 cross-device 兼容垫片
        # （或含 cdp_parse_script 的超集）——若未来有 lib 测试抢先裸 import
        # harness/lib 版，此处判红并指向本文件 docstring 的导入纪律说明
        bare = sys.modules.get("cdp_paths")
        if bare is None:
            self.skipTest("裸名 cdp_paths 未被缓存（单文件直跑场景）")
        self.assertTrue(
            hasattr(bare, "cdp_parse_script"),
            "sys.modules['cdp_paths'] 缺 cdp_parse_script：被 harness/lib "
            "实现抢先缓存，cross-device 垫片语义被污染（详见本文件 docstring）")


class TestAtomicWriteTextConcurrent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "out.txt"

    def test_two_threads_no_interleave_no_tmp_left(self):
        # 两线程同时写同路径各 40 轮：每次读回均为线程 A/B 完整载荷之一
        # （修复前 tmp 互写可产出混合/截断内容），结束后无残留 tmp
        payloads = {tag: _payload(tag) for tag in ("A", "B")}
        barrier = threading.Barrier(2)
        bad_reads = []

        def worker(tag):
            barrier.wait()
            for _ in range(40):
                cdp_paths.atomic_write_text(self.target, payloads[tag])
                got = self.target.read_text(encoding="utf-8")
                if got not in payloads.values():
                    bad_reads.append(got[:60])

        threads = [threading.Thread(target=worker, args=(t,))
                   for t in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(bad_reads, [])
        final = self.target.read_text(encoding="utf-8")
        self.assertIn(final, payloads.values())
        self.assertEqual(list(Path(self._tmp.name).glob("*.tmp")), [])

    def test_tmp_name_contains_thread_ident(self):
        # tmp 名口径：pid + 线程 id（同进程两线程不共用 tmp 文件）
        seen = []
        orig_write = Path.write_text

        def _spy_write(path_self, *a, **kw):
            seen.append(path_self.name)
            return orig_write(path_self, *a, **kw)

        with mock.patch.object(Path, "write_text", _spy_write):
            cdp_paths.atomic_write_text(self.target, "content")
        self.assertEqual(len(seen), 1)
        self.assertIn(str(cdp_paths.os.getpid()), seen[0])
        self.assertIn(str(threading.get_ident()), seen[0])


class TestAtomicWriteJsonConcurrent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "out.json"

    def test_two_threads_final_file_valid_json_no_tmp_left(self):
        # atomic_write_json 两线程并发：最终文件为合法 JSON 且内容为某线程
        # 完整对象（json.loads 成功即无半截态），无残留 tmp
        barrier = threading.Barrier(2)

        def worker(tag):
            barrier.wait()
            for _ in range(20):
                verify_common.atomic_write_json(
                    self.target, {"tag": tag, "pad": "y" * 256})

        threads = [threading.Thread(target=worker, args=(t,))
                   for t in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertIn(data["tag"], ("A", "B"))
        self.assertEqual(len(data["pad"]), 256)
        self.assertEqual(list(Path(self._tmp.name).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()

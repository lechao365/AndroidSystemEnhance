"""paths 单测（批次四 E6 补零覆盖模块）：
repo_root 锚点 / ${VAR:-default} 展开 / conf 缺失告警与降级 / 双名加载共享
同一模块对象（A1 分裂收敛）。

隔离方式：把真实 paths.py 复制进临时仓（harness/lib/ 下）以 spec 加载独立
模块实例——repo_root 以 __file__ 向上找 AGENTS.md，副本的 __file__ 指向
临时仓，conf 读取与告警全部隔离在 tmp；sys.modules 中的真实 paths 不受影响。
"""

import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import paths as real_paths  # 真实模块（别名注册断言用）

_REAL_FILE = Path(real_paths.__file__).resolve()


class TestPathsCore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("KERNEL_WS", "AOSP_WS",
                                 "LC_VERIFY_EXPECT_SERIAL", "MY_TEST_VAR")}
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _load_isolated(self, conf_text=None):
        """临时仓 + spec 独立实例：conf/告警全部隔离在 tmp。"""
        repo = Path(self._tmp.name)
        (repo / "AGENTS.md").write_text("# test\n", encoding="utf-8")
        lib = repo / "harness" / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        shutil.copy(_REAL_FILE, lib / "paths.py")
        if conf_text is not None:
            cfg = repo / "harness" / "config"
            cfg.mkdir(parents=True, exist_ok=True)
            (cfg / "paths.conf").write_text(conf_text, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            "paths_under_test", str(lib / "paths.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_repo_root_finds_agents_md(self):
        # 真仓：AGENTS.md 锚点命中项目根
        self.assertTrue((real_paths.repo_root() / "harness").is_dir())

    def test_env_expansion_with_default(self):
        # ${VAR:-default}：env 有值用 env，无值用默认
        conf = ('KERNEL_WS=${MY_TEST_VAR:-~/workspace/rpi5-kernel-build/common}\n'
                'AOSP_WS=${MY_TEST_VAR:-~/workspace/aosp}\n'
                'LC_VERIFY_EXPECT_SERIAL=\n')
        mod = self._load_isolated(conf)
        self.assertEqual(mod.env_path("KERNEL_WS"),
                         "~/workspace/rpi5-kernel-build/common")
        os.environ["MY_TEST_VAR"] = "/custom/ws"
        mod2 = self._load_isolated(conf)
        self.assertEqual(mod2.env_path("KERNEL_WS"), "/custom/ws")

    def test_path_keyerror_carries_conf_path(self):
        # C3：path() KeyError 消息带 conf 绝对路径（排查直达）
        mod = self._load_isolated("AOSP_WS=~/workspace/aosp\n")
        with self.assertRaises(KeyError) as cm:
            mod.path("KERNEL_WS")
        self.assertIn("paths.conf", str(cm.exception))

    def test_missing_conf_warns_and_degrades(self):
        # C3：conf 缺失首次加载 stderr warn（不再静默降级为空配置）；
        # warn 在首次 _load_conf（惰性）时触发，捕获须覆盖消费调用
        mod = self._load_isolated(None)
        err = io.StringIO()
        with redirect_stderr(err):
            mod.env_path("KERNEL_WS")
        self.assertIn("paths.conf 缺失", err.getvalue())
        self.assertEqual(mod.env_path("KERNEL_WS"), "")

    def test_dual_name_import_shares_module_object(self):
        # A1：from paths import 与 import harness.lib.paths 拿到同一模块
        # 对象（sys.modules 别名注册，_CONF 状态不分裂）
        for p in (str(_REAL_FILE.parents[2]),
                  str(_REAL_FILE.parents[1])):
            if p not in sys.path:
                sys.path.insert(0, p)
        importlib.import_module("harness.lib.paths")
        importlib.import_module("paths")
        m1 = sys.modules["paths"]
        m2 = sys.modules["harness.lib.paths"]
        self.assertIs(m1, m2)

    def test_dual_name_reverse_access_after_short_import(self):
        # 短名先加载（脚本常态：sys.path 注入 lib 后 from paths import），
        # 后续 `import harness.lib.paths` / from harness.lib import paths
        # 须同实例可访问（别名块须补父包属性链，sys.path 无仓库根时仅
        # sys.modules 别名兜底不崩）
        if str(_REAL_FILE.parents[1]) not in sys.path:
            sys.path.insert(0, str(_REAL_FILE.parents[1]))
        importlib.import_module("paths")
        m_short = sys.modules["paths"]
        m_full = importlib.import_module("harness.lib.paths")
        self.assertIs(m_short, m_full)
        from harness.lib import paths as m_from
        self.assertIs(m_from, m_short)


if __name__ == "__main__":
    unittest.main()

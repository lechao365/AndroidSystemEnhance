#!/usr/bin/env python3
"""check_hot_path_scan 单测：治理检查器热路径禁全树 rglob/os.walk。

用临时目录模拟仓（只读文件，无需 git）；清单文件缺失须报（覆盖断链）。
含 tst-02 漂移守卫：从 selfcheck.py 的 _spawn_cmd AST 推导实际 spawn 的
检查器，断言 ⊆ _HOT_PATHS——新增检查器接入 selfcheck 而漏登记清单时判红。"""
import ast
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_hot_path_scan as chps  # noqa: E402

_SELFCHECK_PATH = Path(__file__).resolve().parents[1] / "selfcheck.py"


def _spawned_tool_paths() -> set[str]:
    """AST 推导 selfcheck 实际 spawn 的检查器脚本相对路径（tst-02）。

    从 selfcheck.py 全部 `_spawn_cmd([...])` 调用中提取形如
    `str(ROOT / "harness" / ... / "x.py")` 的脚本表达式，拼接为相对路径。
    若 selfcheck 新增检查器 spawn 而漏登记 _HOT_PATHS，本集合即暴露漂移。
    """
    tree = ast.parse(_SELFCHECK_PATH.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_spawn_cmd"
                and node.args and isinstance(node.args[0], ast.List)):
            continue
        for elt in node.args[0].elts:
            parts = [c.value for c in ast.walk(elt)
                     if isinstance(c, ast.Constant)
                     and isinstance(c.value, str)]
            # ast.walk 是 BFS 序（先内层后外层），parts 顺序与路径段相反：
            # `str(ROOT / "harness" / "lib" / "x.py")` 收集为
            # ['x.py', 'lib', 'harness']，parts[-1] 恒非 .py 结尾（此前全部
            # 推导不出、漂移守卫假绿）。改取 .py 结尾段为脚本名，其余段
            # 逆序拼为目录前缀，拼出真实相对路径（如 harness/lib/x.py）。
            py_parts = [p for p in parts if p.endswith(".py")]
            if not py_parts:
                continue
            fname = py_parts[0]
            dirs = [p for p in parts if p != fname][::-1]
            out.add("/".join(dirs + [fname]))
    # selfcheck 自身亦在守卫面（spawn 工具 import 链源头）
    out.add("harness/lib/selfcheck.py")
    return out


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

    def _git_repo(self, files: dict) -> Path:
        """建临时 git 仓并落盘/提交给定文件，返回仓根（R8 依赖推导用）。

        discover_hot_paths 依赖 git ls-files 列跟踪文件（禁全树遍历），依赖
        推导测试须在真实 git 仓内进行；提交后文件方可被 ls-files 收录。
        """
        import subprocess
        repo = Path(self._tmp.name) / "gitrepo"
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"],
                       check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"],
                       check=True)
        for rel, content in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "t"],
                       check=True)
        return repo

    def test_clean_ok(self):
        # 清单文件合规：零违规
        self.assertEqual(chps.scan(self.repo), [])

    def test_spawned_tools_covered_by_manifest(self):
        # tst-02 漂移守卫：selfcheck 实际 spawn 的所有检查器必须已登记
        # _HOT_PATHS——漏登记则该检查器可任意 rglob/os.walk 而 scan_rc 恒绿
        spawned = _spawned_tool_paths()
        missing = spawned - set(chps._HOT_PATHS)
        self.assertEqual(
            missing, set(),
            f"以下检查器接入 selfcheck 但未登记 _HOT_PATHS（新增治理检查器"
            f"须登记，否则热路径守卫不覆盖）: {sorted(missing)}")
        # 守卫覆盖面必须至少含 spawn 集（方向性：清单非空且含全部分发）
        self.assertTrue(set(chps._HOT_PATHS) >= spawned)

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

    def test_manifest_covers_import_dependencies(self):
        # R8：import 依赖面改由 AST/git 自动推导（不再手工登记 required 集合）。
        # 受守卫工具真实 import 的库文件须在 discover_hot_paths 扫描面内。
        # 注：metrics.py 曾在此清单（7d41df8e24bf 方向 6 锁入），2026-09-15
        # 批次 26cacb40dfd6 摘除 metrics_rc 后不再由 selfcheck spawn，已移除。
        repo = Path(__file__).resolve().parents[3]
        discovered = set(chps.discover_hot_paths(repo))
        required = {
            "harness/lib/harness_lib.py",
            "harness/lib/paths.py",
            "harness/lib/cdp_paths.py",
            "harness/lib/role_guard.py",
            "harness/skills/cross-device/lib/python/cdp_timing.py",
            "harness/skills/cross-device/lib/python/cdp_parse.py",
            "harness/skills/cross-device/lib/python/cdp_paths.py",
            "harness/skills/cross-device/lib/python/cdp_issue.py",
        }
        self.assertTrue(required.issubset(discovered),
                        f"依赖面推导漏文件: {sorted(required - discovered)}")

    def test_import_dependency_auto_registered(self):
        # R8：新增接线点 import 的依赖经 AST 自动纳入扫描面——临时 git 仓放
        # check_config（根，import harness_lib）+ harness_lib（依赖），discover
        # 须自动收录依赖，无需手工登记清单（新增依赖不再漂移漏检）。
        repo = self._git_repo({
            "harness/lib/check_config.py": "import harness_lib\n",
            "harness/lib/harness_lib.py": "VALUE = 1\n",
        })
        discovered = set(chps.discover_hot_paths(repo))
        self.assertIn("harness/lib/harness_lib.py", discovered,
                      "check_config import 的 harness_lib 须自动纳入扫描面")

    def test_dependency_file_rglob_reported(self):
        # R8 红灯：自动纳入的依赖文件内出现裸 rglob → 判红（此前依赖靠手工
        # 登记，漏登记即漂移静默无感；现由 import 图自动纳入并受禁）
        repo = self._git_repo({
            "harness/lib/check_config.py": "import harness_lib\n",
            "harness/lib/harness_lib.py":
                "def f():\n    for x in root.rglob('*'):\n        pass\n",
        })
        out = chps.scan(repo)
        self.assertTrue(any("harness_lib.py" in o and "rglob" in o
                            for o in out), out)


if __name__ == "__main__":
    unittest.main()

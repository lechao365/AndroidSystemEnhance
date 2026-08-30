import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_skill_refs as ckr


class TestCheckSkillRefs(unittest.TestCase):
    def setUp(self):
        self._orig_root = ckr.ROOT
        self.tmp = Path(tempfile.mkdtemp())
        ckr.ROOT = self.tmp

    def tearDown(self):
        ckr.ROOT = self._orig_root

    def _mk(self, rel, content):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _scan(self, rel):
        f = self.tmp / rel
        return ckr.scan_file(f)

    def test_valid_link_not_reported(self):
        self._mk("harness/skills/demo/SKILL.md",
                 "[a](../other/doc.md) [b](harness/skills/demo/run.py)")
        self._mk("harness/skills/demo/run.py", "#!/usr/bin/env python3\n")
        self._mk("harness/skills/other/doc.md", "x\n")
        self.assertEqual(self._scan("harness/skills/demo/SKILL.md"), [])

    def test_missing_link_reported(self):
        self._mk("harness/skills/demo/SKILL.md", "[a](../other/not-exist.md)")
        self.assertEqual(self._scan("harness/skills/demo/SKILL.md"),
                         ["../other/not-exist.md"])

    def test_anchor_stripped_not_reported(self):
        # #L 行号锚点剥离后基础文件存在则不算悬空
        self._mk("harness/skills/demo/SKILL.md",
                 "[a](../other/doc.md#L3)")
        self._mk("harness/skills/other/doc.md", "x\ny\nz\n")
        self.assertEqual(self._scan("harness/skills/demo/SKILL.md"), [])

    def test_placeholder_ignored(self):
        # 格式模板占位符（中文/省略号）不报
        self._mk("harness/skills/demo/SKILL.md",
                 "| A | `[file:行](路径#L行)` | `[x](...#L248)` |")
        self.assertEqual(self._scan("harness/skills/demo/SKILL.md"), [])

    def test_command_path_checked(self):
        self._mk("harness/skills/demo/SKILL.md",
                 "python3 harness/skills/demo/run.py")
        self._mk("harness/skills/demo/run.py", "x\n")
        self.assertEqual(self._scan("harness/skills/demo/SKILL.md"), [])
        # 命令指向不存在脚本 → 报
        self._mk("harness/skills/demo/SKILL.md",
                 "bash harness/skills/demo/missing.sh")
        self.assertEqual(self._scan("harness/skills/demo/SKILL.md"),
                         ["harness/skills/demo/missing.sh"])

    def test_script_path_string_checked(self):
        self._mk("harness/skills/demo/run.py",
                 'p = "harness/skills/demo/helper.py"')
        self._mk("harness/skills/demo/helper.py", "x\n")
        self.assertEqual(self._scan("harness/skills/demo/run.py"), [])
        self._mk("harness/skills/demo/run2.py",
                 'p = "harness/skills/demo/none.py"')
        self.assertEqual(self._scan("harness/skills/demo/run2.py"),
                         ["harness/skills/demo/none.py"])

    def test_tests_dir_skipped(self):
        # tests/ 下 mock 失效链接属测试故意构造，不纳入
        self._mk("harness/skills/demo/tests/test_demo.py",
                 '"[missing](./missing.md)" "x"\n'
                 "[dead](../../code/nope.c#L1)\n"
                 "import os, sys, unittest")
        targets = ckr.iter_scan_targets(None)
        self.assertTrue(all("tests" not in t.parts for t in targets))

    def test_iter_scan_targets_excludes_caches(self):
        self._mk("harness/skills/demo/SKILL.md", "ok\n")
        self._mk("harness/skills/demo/__pycache__/x.pyc", "")
        targets = ckr.iter_scan_targets(None)
        rels = [str(t.relative_to(self.tmp)) for t in targets]
        self.assertIn("harness/skills/demo/SKILL.md", rels)
        self.assertNotIn("harness/skills/demo/__pycache__/x.pyc", rels)

    def test_command_file_at_ref(self):
        self._mk("harness/skills/demo/SKILL.md", "x\n")
        self._mk("harness/skills/demo/run.py", "x\n")
        self._mk(".opencode/command/demo.md",
                 "@harness/skills/demo/SKILL.md\n"
                 "!`python3 harness/skills/demo/run.py $ARGUMENTS`\n")
        self.assertEqual(ckr.scan_command_files(), [])
        self._mk(".opencode/command/bad.md", "@harness/skills/gone/SKILL.md\n")
        out = ckr.scan_command_files()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1], ["harness/skills/gone/SKILL.md"])

    def test_path_mode_single_file(self):
        # --path 单文件模式：只扫描指定文件
        self._mk("harness/skills/a/SKILL.md", "[x](../b/m.md)")
        self._mk("harness/skills/b/m.md", "x\n")
        targets = ckr.iter_scan_targets("harness/skills/a/SKILL.md")
        self.assertEqual([str(t.relative_to(self.tmp)) for t in targets],
                         ["harness/skills/a/SKILL.md"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""check_quotepath 单测：禁裸 git diff/ls-files/status 未带 core.quotepath=false。

用临时真 git 仓模拟（scan 经 git ls-files 列文件面）。判红语义只静态判定
命令文本（与运行环境 git 全局配置无关）——即使 apply 机全局 quotepath=false
掩盖非 ASCII 转义，裸调用在检查器下恒判红（方向 2：须以 -c core.quotepath=true
实测门禁真有效）。"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_quotepath as cqp  # noqa: E402


class TestCheckQuotepath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email",
                        "t@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name",
                        "t"], check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _add(self, rel: str, content: str):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", rel], check=True)
        # 方向 3：默认 .sh 以 100755 入库（净克隆可执行）；判红用例显式 -x
        if rel.endswith(".sh"):
            subprocess.run(["git", "-C", str(self.repo), "update-index",
                            "--chmod=+x", rel], check=True)

    def test_clean_ok(self):
        self._add("harness/lib/a.py", "def f():\n    return 1\n")
        self._add("harness/tool.sh", "#!/bin/bash\necho hi\n")
        self.assertEqual(cqp.scan(self.repo), [])

    def test_py_direct_git_diff_reported(self):
        # 直接调用 ["git", "diff", ...]（content_tree diff_paths 同形态）判红
        self._add("harness/lib/a.py",
                  "import subprocess\n"
                  "r = subprocess.run(['git', 'diff', '--name-only', 'a', 'b'],\n"
                  "                   capture_output=True)\n")
        out = cqp.scan(self.repo)
        self.assertTrue(any("a.py" in o and "diff" in o for o in out))

    def test_py_wrapper_git_args_reported(self):
        # 通用封装 ["git", *args]（_git_lines/_run_git 同形态）：内部未带 -c
        # 则全部调用点路径输出均被转义 → 判红
        self._add("harness/lib/b.py",
                  "import subprocess\n"
                  "def run(args):\n"
                  "    return subprocess.run(['git', *args], capture_output=True)\n")
        self.assertTrue(any("b.py" in o for o in cqp.scan(self.repo)))

    def test_sh_command_position_reported(self):
        # .sh 命令位置裸 git status/diff/ls-files → 判红
        self._add("harness/tool.sh",
                  "#!/bin/bash\n"
                  "git status --porcelain | tee -a log\n"
                  "git diff HEAD --stat\n"
                  "git ls-files --others --exclude-standard\n")
        out = cqp.scan(self.repo)
        sh_hits = [o for o in out if "tool.sh" in o]
        self.assertTrue(any("status" in o for o in sh_hits))
        self.assertTrue(any("diff" in o for o in sh_hits))
        self.assertTrue(any("ls-files" in o for o in sh_hits))

    def test_with_quotepath_allowed(self):
        # 同一调用带 -c core.quotepath=false → 放行
        self._add("harness/lib/a.py",
                  "import subprocess\n"
                  "r = subprocess.run(['git', '-c', 'core.quotepath=false',\n"
                  "                   'diff', '--name-only', 'a', 'b'],\n"
                  "                   capture_output=True)\n")
        self._add("harness/tool.sh",
                  "#!/bin/bash\ngit -c core.quotepath=false status --porcelain\n")
        self.assertEqual(cqp.scan(self.repo), [])

    def test_comment_line_ignored(self):
        # 纯注释行提及 git diff 非解析点
        self._add("harness/lib/a.py",
                  "# 注意：不要裸调 git diff，非 ASCII 会被转义\n"
                  "def f():\n    return 1\n")
        self.assertEqual(cqp.scan(self.repo), [])

    def test_docstring_line_ignored(self):
        # docstring 中间行引用 git diff（tokenize 排除）非解析点
        self._add("harness/lib/a.py",
                  '"""本批改动文件（git diff --name-only HEAD）。\n'
                  "多行文档中间行：git ls-files 也仅是引用。\n"
                  '"""\n'
                  "def f():\n    return 1\n")
        self.assertEqual(cqp.scan(self.repo), [])

    def test_error_message_text_ignored(self):
        # 错误消息字符串（非命令位）：.py 列表字面判定不命中
        self._add("harness/lib/a.py",
                  'print("error: git ls-files 失败，无法扫描，判红")\n')
        self.assertEqual(cqp.scan(self.repo), [])

    def test_sh_error_message_text_ignored(self):
        # .sh 错误消息引号内 git status（git 前是引号，非命令位）不判红
        self._add("harness/tool.sh",
                  '#!/bin/bash\necho "error: git status 失败" >&2\nexit 1\n')
        self.assertEqual(cqp.scan(self.repo), [])

    def test_sh_var_context_not_reported(self):
        # git 出现在变量名/非命令位（err "git status 失败" 形态）不判红
        self._add("harness/tool.sh",
                  '#!/bin/bash\nerr() { echo "$*" >&2; }\nerr "git status 失败"\n')
        self.assertEqual(cqp.scan(self.repo), [])

    def test_non_scan_file_skipped(self):
        # tests/ 下与 .pyc 不扫描（生产代码守卫生效）
        self._add("harness/lib/tests/test_a.py",
                  "r = subprocess.run(['git', 'diff', 'a', 'b'])\n")
        self._add("harness/lib/x.pyc", "\x00")
        self.assertEqual(cqp.scan(self.repo), [])

    def test_git_failure_fail_closed(self):
        # 非 git 仓（ls-files 失败）→ fail-closed 哨兵判红，不静默放行。
        # 目录须在 git 仓外（仓内子目录会被向上 .git 识别为非失败）
        with tempfile.TemporaryDirectory() as d:
            empty = Path(d) / "nongit"
            empty.mkdir()
            (empty / "harness").mkdir()
            out = cqp.scan(empty)
        self.assertTrue(any("git ls-files 失败" in o for o in out))

    def test_scan_face_covers_all_four_forms(self):
        # 方向 1（KI 2026-09-11 第二种形态）：扫描面完整性——四种拼法各造
        # 一例断言判红。上批只证"判红有效"，未证"扫描完整"——检查器只认
        # [git, diff...] 与 [git, *args]，漏 [git]+args 与 [git,-C,root,*args]
        # 包装器（sync_code_to_workspace/sync_code_to_doc/cdp_apply/emit
        # precheck），四个包装器下上百个调用点全不可见（quotepath_rc=0 假绿）
        forms = [
            'r = subprocess.run(["git", "diff", "--name-only", "a", "b"])\n',
            'r = subprocess.run(["git", *args])\n',
            'r = subprocess.run(["git"] + args)\n',
            'r = subprocess.run(["git", "-C", str(root), *args])\n',
        ]
        for i, sample in enumerate(forms):
            rel = f"harness/lib/w{i}.py"
            self._add(rel, "import subprocess\n" + sample)
        out = cqp.scan(self.repo)
        for i in range(len(forms)):
            self.assertTrue(
                any(f"w{i}.py" in o for o in out),
                f"拼法 {i + 1} 未判红（扫描面不完整）: {forms[i].strip()}")

    def test_sh_not_executable_reported(self):
        # 方向 3：.sh 以 100644 入库判红（drvfs 本机看着可执行、净克隆暴露）
        self._add("harness/tool.sh", "#!/bin/bash\necho hi\n")
        subprocess.run(["git", "-C", str(self.repo), "update-index",
                        "--chmod=-x", "harness/tool.sh"], check=True)
        out = cqp.scan(self.repo)
        self.assertTrue(any("100644" in o and "tool.sh" in o for o in out))

    def test_sh_executable_allowed(self):
        # 100755 的 .sh 放行（干净仓已有用例覆盖；显式再证）
        self._add("harness/tool.sh", "#!/bin/bash\necho hi\n")
        subprocess.run(["git", "-C", str(self.repo), "update-index",
                        "--chmod=+x", "harness/tool.sh"], check=True)
        self.assertEqual([o for o in cqp.scan(self.repo) if "tool.sh" in o], [])

    def test_gate_true_verification(self):
        # 方向 2：以 -c core.quotepath=true 实测门禁真有效——同一文件裸调用
        # 判红、补 -c 后放行（判定与运行环境全局 quotepath 配置无关，静态
        # 判定命令文本，apply 机全局 false 掩盖不了门禁）
        rel = "harness/tool.sh"
        self._add(rel, "#!/bin/bash\ngit diff HEAD --stat\n")
        self.assertTrue(any(rel in o for o in cqp.scan(self.repo)))
        (self.repo / rel).write_text(
            "#!/bin/bash\ngit -c core.quotepath=false diff HEAD --stat\n",
            encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", rel], check=True)
        subprocess.run(["git", "-C", str(self.repo), "update-index",
                        "--chmod=+x", rel], check=True)
        self.assertEqual(
            [o for o in cqp.scan(self.repo) if rel in o], [])


if __name__ == "__main__":
    unittest.main()

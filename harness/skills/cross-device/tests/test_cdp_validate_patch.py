import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_validate_patch as cv

GOOD = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,1 +10,3 @@ static int x;
 int y;
+
+/* new line */
"""

GOOD_NEW_FILE = """diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+first
+second
"""

GOOD_NO_NEWLINE = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,1 +10,1 @@ static int x;
-old
+new
\\ No newline at end of file
"""

GOOD_MULTI_FILE = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,1 +10,2 @@ static int x;
 int y;
+int z;
diff --git a/bar.c b/bar.c
index 3333333..4444444 100644
--- a/bar.c
+++ b/bar.c
@@ -1,1 +1,1 @@ int b;
-old
+new
"""

BAD_MISSING_HUNK = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
no hunk here
"""

BAD_CONTEXT_LINE = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,3 +10,5 @@ static int x;
this line has no prefix
"""

BAD_COUNT = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,1 +10,1 @@ static int x;
 int y;
+/* extra */
"""

BAD_BAD_HUNK = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -10,1 +10,1 @@ static int x;
 int y;
@@ broken
+/* extra */
"""

BAD_HEADER_ONLY = """diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
"""

BAD_BINARY = """diff --git a/foo.bin b/foo.bin
index 1111111..2222222 100644
Binary files a/foo.bin and b/foo.bin differ
"""

BAD_BINARY_PATCH = """diff --git a/foo.bin b/foo.bin
index 1111111..2222222 100644
GIT binary patch
literal 5
..."""


class TestValidatePatch(unittest.TestCase):
    def _write(self, content):
        f = tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False,
                                        encoding="utf-8", newline="\n")
        f.write(content)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_good_modified(self):
        ok, errs = cv.validate_diff(self._write(GOOD))
        self.assertTrue(ok, errs)

    def test_good_new_file(self):
        # new file mode 行必须被接受（否则所有新建文件 diff 被误拒）
        ok, errs = cv.validate_diff(self._write(GOOD_NEW_FILE))
        self.assertTrue(ok, errs)

    def test_good_no_newline_marker(self):
        ok, errs = cv.validate_diff(self._write(GOOD_NO_NEWLINE))
        self.assertTrue(ok, errs)

    def test_good_multi_file(self):
        # 一个 diff 文件含多个 diff --git 块（多文件 diff）：各块独立校验均须通过
        ok, errs = cv.validate_diff(self._write(GOOD_MULTI_FILE))
        self.assertTrue(ok, errs)

    def test_binary_rejected(self):
        # Binary files 元信息行：立即拒绝，仅单条专用错误（无 hunk 头等误导错误）
        ok, errs = cv.validate_diff(self._write(BAD_BINARY))
        self.assertFalse(ok)
        self.assertEqual(len(errs), 1)
        self.assertTrue(any("不支持二进制" in e for e in errs))

    def test_git_binary_patch_rejected(self):
        # GIT binary patch 形态：检出即立即拒绝，仅单条专用错误
        ok, errs = cv.validate_diff(self._write(BAD_BINARY_PATCH))
        self.assertFalse(ok)
        self.assertEqual(len(errs), 1)
        self.assertTrue(any("不支持二进制" in e for e in errs))
        self.assertFalse(any("hunk" in e for e in errs))

    def test_missing_hunk(self):
        ok, errs = cv.validate_diff(self._write(BAD_MISSING_HUNK))
        self.assertFalse(ok)
        self.assertTrue(any("hunk" in e or "非法" in e for e in errs))

    def test_bad_line_prefix(self):
        ok, errs = cv.validate_diff(self._write(BAD_CONTEXT_LINE))
        self.assertFalse(ok)
        self.assertTrue(any("前缀" in e for e in errs))

    def test_hunk_count_mismatch(self):
        # hunk 声明行数与实体不符 → 拒（corrupt patch 漏网防护）
        ok, errs = cv.validate_diff(self._write(BAD_COUNT))
        self.assertFalse(ok)
        self.assertTrue(any("行数不符" in e for e in errs))

    def test_header_only_rejected(self):
        # 无任何 @@ hunk 头的 header-only diff → 拒
        ok, errs = cv.validate_diff(self._write(BAD_HEADER_ONLY))
        self.assertFalse(ok)
        self.assertTrue(any("hunk" in e for e in errs))

    def test_bad_hunk_header_rejected(self):
        # @@ 开头但不匹配合法 hunk 头的行（_META_RE 已不含 @@）→ 显式报错
        ok, errs = cv.validate_diff(self._write(BAD_BAD_HUNK))
        self.assertFalse(ok)
        self.assertTrue(any("@@ 开头但非合法 hunk 头" in e for e in errs))

    def test_crlf_rejected(self):
        # 含 CRLF 行尾的 diff 必须被拒（须为 LF）
        f = tempfile.NamedTemporaryFile("wb", suffix=".diff", delete=False)
        f.write(GOOD.replace("\n", "\r\n").encode("utf-8"))
        f.close()
        self.addCleanup(Path(f.name).unlink)
        ok, errs = cv.validate_diff(f.name)
        self.assertFalse(ok)
        self.assertTrue(any("CRLF" in e for e in errs))


class TestValidateAgainst(unittest.TestCase):
    """--against：对每个 diff 额外跑 git apply --check 语义校验。"""

    def _git_repo(self, content="int y;\n"):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q", root], check=True)
        subprocess.run(["git", "-C", root, "config", "user.email", "t@t"],
                       check=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "t"],
                       check=True)
        (root / "foo.c").write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", "foo.c"], check=True)
        subprocess.run(["git", "-C", root, "commit", "-qm", "init"], check=True)
        return root

    def _write(self, content):
        f = tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False,
                                        encoding="utf-8", newline="\n")
        f.write(content)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_against_applies_cleanly(self):
        # 上下文匹配的合法 diff：git apply --check 通过 → exit 0
        root = self._git_repo()
        d = self._write("""diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -1 +1,2 @@
 int y;
+int z;
""")
        self.assertEqual(cv.main(["--against", str(root), d]), 0)

    def test_against_context_mismatch_rejected(self):
        # 格式合法但上下文不匹配（context 行与仓库内容不符）：apply --check 拒绝
        root = self._git_repo()
        d = self._write("""diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -1 +1,2 @@
 different;
+int z;
""")
        self.assertEqual(cv.main(["--against", str(root), d]), 1)

    def test_without_against_unchanged(self):
        # 未传 --against：仅格式校验，上下文失配 diff 仍 exit 0（行为不变）
        d = self._write("""diff --git a/foo.c b/foo.c
index 1111111..2222222 100644
--- a/foo.c
+++ b/foo.c
@@ -1 +1,2 @@
 different;
+int z;
""")
        self.assertEqual(cv.main([d]), 0)


if __name__ == "__main__":
    unittest.main()
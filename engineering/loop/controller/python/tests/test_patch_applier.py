"""patch_applier 单元测试。"""
import subprocess

from loop_controller.patch_applier import apply_file_changes
from loop_controller.analyzer_protocol import FileChange


def test_edit_unique_marker(tmp_path):
    f = tmp_path / "test.cpp"
    f.write_text("int x = 1;\nint y = 2;\n")
    changes = [FileChange(workspace_path="test.cpp", change_type="edit",
                          old_marker="int x = 1;", new_content="int x = 42;")]
    result = apply_file_changes(changes, str(tmp_path))
    assert result.success
    assert "int x = 42;" in f.read_text()


def test_edit_not_found(tmp_path):
    f = tmp_path / "test.cpp"
    f.write_text("int a = 1;\n")
    changes = [FileChange(workspace_path="test.cpp", old_marker="not_there", new_content="x")]
    result = apply_file_changes(changes, str(tmp_path))
    assert not result.success
    assert "not found" in result.error


def test_edit_duplicate_marker(tmp_path):
    f = tmp_path / "test.cpp"
    f.write_text("dup\ndup\n")
    changes = [FileChange(workspace_path="test.cpp", old_marker="dup", new_content="fixed")]
    result = apply_file_changes(changes, str(tmp_path))
    assert not result.success
    assert "2 times" in result.error


def test_create_file(tmp_path):
    changes = [FileChange(workspace_path="new.txt", change_type="create", new_content="hello")]
    result = apply_file_changes(changes, str(tmp_path))
    assert result.success
    assert (tmp_path / "new.txt").read_text() == "hello"


def test_empty_changes(tmp_path):
    result = apply_file_changes([], str(tmp_path))
    assert result.success


# ---------------------------------------------------------------------------
# line_range 模式：按行号区间替换
# ---------------------------------------------------------------------------

def test_line_range_edit(tmp_path):
    f = tmp_path / "test.c"
    f.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
    change = FileChange(
        workspace_path="test.c",
        line_range=(2, 3),
        new_content="REPLACED\n",
    )
    result = apply_file_changes([change], str(tmp_path))
    assert result.success
    assert f.read_text() == "line1\nREPLACED\nline4\n"


# ---------------------------------------------------------------------------
# unified diff 模式：git apply
# ---------------------------------------------------------------------------

def test_unified_diff_edit(tmp_path):
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    f = tmp_path / "test.c"
    f.write_text("old line\n", encoding="utf-8")
    subprocess.run(["git", "add", "test.c"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    diff = "--- a/test.c\n+++ b/test.c\n@@ -1 +1 @@\n-old line\n+new line\n"
    change = FileChange(workspace_path="test.c", diff=diff)
    result = apply_file_changes([change], str(tmp_path))
    assert result.success
    assert "new line" in f.read_text()


# ---------------------------------------------------------------------------
# marker 模式向后兼容
# ---------------------------------------------------------------------------

def test_marker_mode_still_works(tmp_path):
    f = tmp_path / "test.c"
    f.write_text("hello world\n", encoding="utf-8")
    change = FileChange(workspace_path="test.c", old_marker="hello", new_content="goodbye")
    result = apply_file_changes([change], str(tmp_path))
    assert result.success
    assert "goodbye world" in f.read_text()


# ---------------------------------------------------------------------------
# diff 优先级高于 marker
# ---------------------------------------------------------------------------

def test_diff_mode_priority_over_marker(tmp_path):
    """diff 非空时优先用 diff，忽略 old_marker。"""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    f = tmp_path / "test.c"
    f.write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "test.c"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    diff = "--- a/test.c\n+++ b/test.c\n@@ -1 +1 @@\n-a\n+b\n"
    change = FileChange(workspace_path="test.c", diff=diff, old_marker="should_be_ignored")
    result = apply_file_changes([change], str(tmp_path))
    assert result.success

"""patch_applier 单元测试。"""
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

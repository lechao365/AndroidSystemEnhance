"""patch_guard 单元测试。"""
from loop_controller.patch_guard import check_white_list, detect_risk, check_syntax
from loop_controller.analyzer_protocol import FileChange


def test_white_list_allows_known_path():
    allowed = ["vendor/lechao/services/lechao_lciod/"]
    changes = [FileChange(workspace_path="vendor/lechao/services/lechao_lciod/hal/hal_service.cpp")]
    result = check_white_list(changes, allowed)
    assert result.allowed is True


def test_white_list_rejects_unknown_path():
    allowed = ["vendor/lechao/services/lechao_lciod/"]
    changes = [FileChange(workspace_path="vendor/other_module/foo.cpp")]
    result = check_white_list(changes, allowed)
    assert result.allowed is False
    assert "vendor/other_module/foo.cpp" in result.rejected_files


def test_white_list_partial_reject():
    allowed = ["vendor/lechao/services/lechao_lciod/"]
    changes = [
        FileChange(workspace_path="vendor/lechao/services/lechao_lciod/hal/hal_service.cpp"),
        FileChange(workspace_path="vendor/other_module/foo.c"),
    ]
    result = check_white_list(changes, allowed)
    assert result.allowed is False
    assert len(result.rejected_files) == 1


def test_white_list_empty_changes():
    result = check_white_list([], ["any/"])
    assert result.allowed is True


def test_white_list_empty_prefixes_rejects_all():
    result = check_white_list([FileChange(workspace_path="a/b.cpp")], [])
    assert result.allowed is False


def test_white_list_prefix_exact_dir_match():
    allowed = ["vendor/lechao/services/lechao_lciod/"]
    changes = [FileChange(workspace_path="vendor/lechao/services/lechao_lciod")]
    result = check_white_list(changes, allowed)
    assert result.allowed is True


def test_detect_risk_normal():
    changes = [FileChange(workspace_path="vendor/foo/bar.cpp")]
    assert detect_risk(changes) == "NORMAL"


def test_detect_risk_kernel_c():
    changes = [FileChange(workspace_path="kernel/drivers/foo.c")]
    assert detect_risk(changes) == "KERNEL"


def test_detect_risk_makefile():
    changes = [FileChange(workspace_path="kernel/Makefile")]
    assert detect_risk(changes) == "KERNEL"


def test_detect_risk_rc():
    changes = [FileChange(workspace_path="vendor/lechao/init.lechao.rc")]
    assert detect_risk(changes) == "KERNEL"


def test_detect_risk_empty():
    assert detect_risk([]) == "NORMAL"


def test_check_syntax_ok(tmp_path):
    f = tmp_path / "ok.cpp"
    f.write_text("int x = 1;\n", encoding="utf-8")
    changes = [FileChange(workspace_path="ok.cpp")]
    errors = check_syntax(changes, str(tmp_path))
    assert errors == []


def test_check_syntax_error(tmp_path):
    f = tmp_path / "bad.c"
    f.write_text("int x = ;\n", encoding="utf-8")
    changes = [FileChange(workspace_path="bad.c")]
    errors = check_syntax(changes, str(tmp_path))
    assert len(errors) == 1
    assert "bad.c" in errors[0]


def test_check_syntax_skips_non_c_files(tmp_path):
    changes = [FileChange(workspace_path="readme.md")]
    errors = check_syntax(changes, str(tmp_path))
    assert errors == []


def test_check_syntax_skips_missing_file(tmp_path):
    changes = [FileChange(workspace_path="absent.c")]
    errors = check_syntax(changes, str(tmp_path))
    assert errors == []

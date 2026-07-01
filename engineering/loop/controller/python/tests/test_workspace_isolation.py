"""workspace_isolation 测试：基于 git worktree 的补丁隔离。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loop_controller.workspace_isolation import (
    WorktreeHandle,
    create_patch_worktree,
    remove_patch_worktree,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _worktree_list(repo: Path) -> list[str]:
    out = _git(repo, "worktree", "list", "--porcelain")
    return [line.split()[1] for line in out.splitlines() if line.startswith("worktree ")]


def test_create_patch_worktree_creates_dir_and_branch(git_repo: Path):
    handle = create_patch_worktree(str(git_repo), "sess-1", 0)

    assert isinstance(handle, WorktreeHandle)
    assert handle.created is True
    assert handle.workspace_root == str(git_repo)
    assert handle.branch == "loop/sess-1/0"

    wt_path = Path(handle.worktree_path)
    assert wt_path.is_dir(), f"worktree dir not created: {wt_path}"
    assert (wt_path / "a.txt").read_text() == "init"

    cur_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=wt_path, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert cur_branch == handle.branch


def test_create_patch_worktree_appears_in_worktree_list(git_repo: Path):
    handle = create_patch_worktree(str(git_repo), "sess-1", 0)
    listed = _worktree_list(git_repo)
    assert handle.worktree_path in listed


def test_create_patch_worktree_default_parent_dir(git_repo: Path):
    handle = create_patch_worktree(str(git_repo), "sess-1", 3)
    expected_parent = git_repo.parent / ".loop-worktrees"
    assert Path(handle.worktree_path).parent == expected_parent


def test_create_patch_worktree_custom_parent_dir(git_repo: Path, tmp_path: Path):
    custom_parent = tmp_path / "custom-wt"
    handle = create_patch_worktree(
        str(git_repo), "sess-1", 0, worktree_parent=str(custom_parent)
    )
    assert Path(handle.worktree_path).parent == custom_parent


def test_create_patch_worktree_idempotent_when_exists(git_repo: Path):
    first = create_patch_worktree(str(git_repo), "sess-1", 0)
    second = create_patch_worktree(str(git_repo), "sess-1", 0)

    assert second.created is False
    assert second.worktree_path == first.worktree_path
    assert second.branch == first.branch

    assert len(_worktree_list(git_repo)) == 2


def test_create_patch_worktree_distinct_attempts_distinct_paths(git_repo: Path):
    h0 = create_patch_worktree(str(git_repo), "sess-1", 0)
    h1 = create_patch_worktree(str(git_repo), "sess-1", 1)
    assert h0.worktree_path != h1.worktree_path
    assert h0.branch != h1.branch


def test_create_patch_worktree_raises_on_non_git_dir(tmp_path: Path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    with pytest.raises(RuntimeError) as exc_info:
        create_patch_worktree(str(not_a_repo), "sess-1", 0)
    assert "not a git repository" in str(exc_info.value)


def test_remove_patch_worktree_removes_and_returns_true(git_repo: Path):
    handle = create_patch_worktree(str(git_repo), "sess-1", 0)
    ok = remove_patch_worktree(handle)
    assert ok is True

    listed = _worktree_list(git_repo)
    assert handle.worktree_path not in listed


def test_remove_patch_worktree_path_gone_after_remove(git_repo: Path):
    handle = create_patch_worktree(str(git_repo), "sess-1", 0)
    remove_patch_worktree(handle)
    assert not Path(handle.worktree_path).exists()


def test_remove_patch_worktree_returns_false_on_missing(git_repo: Path):
    handle = create_patch_worktree(str(git_repo), "sess-1", 0)
    remove_patch_worktree(handle)

    ok = remove_patch_worktree(handle)
    assert ok is False


def test_remove_patch_worktree_does_not_raise_on_bad_handle(tmp_path: Path):
    handle = WorktreeHandle(
        worktree_path=str(tmp_path / "does-not-exist"),
        branch="loop/none/0",
        workspace_root=str(tmp_path),
        created=False,
    )
    ok = remove_patch_worktree(handle)
    assert ok is False


def test_create_patch_worktree_with_candidate_id(tmp_path: Path):
    """G2: create_patch_worktree 支持 candidate_id 参数，命名包含候选维度。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(ws), capture_output=True)
    (ws / "README").write_text("init")
    subprocess.run(["git", "add", "."], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), capture_output=True)

    handle = create_patch_worktree(
        str(ws), "sess-001", 1, candidate_id="c0",
        worktree_parent=str(tmp_path / "wt"),
    )
    assert "c0" in handle.worktree_path
    assert "c0" in handle.branch
    assert handle.created

    from loop_controller.workspace_isolation import remove_patch_worktree
    remove_patch_worktree(handle)


def test_create_patch_worktree_without_candidate_id_backward_compat(tmp_path: Path):
    """G2: candidate_id 为空时退化为现有命名（向后兼容）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(ws), capture_output=True)
    (ws / "README").write_text("init")
    subprocess.run(["git", "add", "."], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), capture_output=True)

    handle = create_patch_worktree(
        str(ws), "sess-002", 2,
        worktree_parent=str(tmp_path / "wt"),
    )
    assert handle.worktree_path.endswith("sess-002_2")
    assert handle.branch.endswith("sess-002/2")

    from loop_controller.workspace_isolation import remove_patch_worktree
    remove_patch_worktree(handle)

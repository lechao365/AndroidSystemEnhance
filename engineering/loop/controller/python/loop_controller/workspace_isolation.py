"""workspace_isolation：基于 git worktree 的补丁工作区隔离。

每次 attempt 在独立 worktree 中应用补丁，避免多次改动混在同一工作区、
绕开 stash 栈语义脆弱的问题。生命周期：
- 成功后立即清理 worktree
- 失败的 worktree 保留供 debug
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorktreeHandle:
    worktree_path: str
    branch: str
    workspace_root: str
    created: bool


_DEFAULT_WORKTREE_PARENT_DIRNAME = ".loop-worktrees"


def _run_git(cwd: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )


def _is_git_repo(workspace_root: str) -> bool:
    res = _run_git(workspace_root, ["rev-parse", "--is-inside-work-tree"])
    return res.returncode == 0 and res.stdout.strip() == "true"


def _worktree_list_paths(workspace_root: str) -> list[str]:
    res = _run_git(workspace_root, ["worktree", "list", "--porcelain"])
    if res.returncode != 0:
        return []
    paths: list[str] = []
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(line.split(maxsplit=1)[1])
    return paths


def create_patch_worktree(
    workspace_root: str,
    session_id: str,
    attempt_index: int,
    worktree_parent: str = "",
) -> WorktreeHandle:
    """为单次 attempt 创建独立 worktree，分支名 loop/<session_id>/<attempt_index>。

    幂等：若 worktree 已存在则直接返回现有 handle（created=False）。
    非法入参（非 git 目录）抛 RuntimeError。
    """
    if not _is_git_repo(workspace_root):
        raise RuntimeError(
            f"workspace_root is not a git repository: {workspace_root}"
        )

    parent = Path(worktree_parent) if worktree_parent else (
        Path(workspace_root).parent / _DEFAULT_WORKTREE_PARENT_DIRNAME
    )
    wt_path = parent / f"{session_id}_{attempt_index}"
    branch = f"loop/{session_id}/{attempt_index}"

    if str(wt_path) in _worktree_list_paths(workspace_root):
        return WorktreeHandle(
            worktree_path=str(wt_path),
            branch=branch,
            workspace_root=workspace_root,
            created=False,
        )

    wt_path.parent.mkdir(parents=True, exist_ok=True)
    res = _run_git(workspace_root, ["worktree", "add", "-b", branch, str(wt_path)])
    if res.returncode != 0:
        if "already exists" in res.stderr and str(wt_path) in _worktree_list_paths(
            workspace_root
        ):
            return WorktreeHandle(
                worktree_path=str(wt_path),
                branch=branch,
                workspace_root=workspace_root,
                created=False,
            )
        raise RuntimeError(
            f"git worktree add failed: {res.stderr.strip() or res.stdout.strip()}"
        )

    return WorktreeHandle(
        worktree_path=str(wt_path),
        branch=branch,
        workspace_root=workspace_root,
        created=True,
    )


def remove_patch_worktree(handle: WorktreeHandle) -> bool:
    """移除 worktree 并删除其分支。失败不抛异常，返回 False。"""
    try:
        rm = _run_git(
            handle.workspace_root,
            ["worktree", "remove", "--force", handle.worktree_path],
        )
        if rm.returncode != 0:
            if handle.worktree_path not in _worktree_list_paths(handle.workspace_root):
                return False
            return False

        _run_git(
            handle.workspace_root,
            ["branch", "-D", handle.branch],
        )
        return True
    except Exception:
        return False

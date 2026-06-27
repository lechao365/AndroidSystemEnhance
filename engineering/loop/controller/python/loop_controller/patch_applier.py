"""patch_applier：将 FileChange 列表应用到 workspace 源码。"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from loop_controller.analyzer_protocol import FileChange


@dataclass
class ApplyResult:
    success: bool
    applied_files: list[str] = field(default_factory=list)
    error: str = ""
    git_diff: str = ""


def apply_file_changes(changes: list[FileChange], workspace_root: str) -> ApplyResult:
    if not changes:
        return ApplyResult(success=True)

    applied = []
    for fc in changes:
        fp = Path(workspace_root) / fc.workspace_path

        if fc.change_type == "edit":
            if not fp.exists():
                return ApplyResult(success=False, applied_files=applied,
                                   error=f"file not found: {fc.workspace_path}")
            if fc.diff:
                err = _apply_diff(fc.diff, workspace_root)
                if err:
                    return ApplyResult(success=False, applied_files=applied, error=err)
            elif fc.line_range:
                err = _apply_line_range(fp, fc.line_range, fc.new_content)
                if err:
                    return ApplyResult(success=False, applied_files=applied, error=err)
            else:
                err = _apply_marker(fp, fc.old_marker, fc.new_content, fc.workspace_path)
                if err:
                    return ApplyResult(success=False, applied_files=applied, error=err)
        elif fc.change_type == "create":
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(fc.new_content, encoding="utf-8")
        elif fc.change_type == "delete":
            if fp.exists():
                fp.unlink()
        applied.append(fc.workspace_path)

    return ApplyResult(success=True, applied_files=applied)


def _apply_diff(diff: str, workspace_root: str) -> str:
    """应用 unified diff，优先 git apply，失败回退 patch -p1。"""
    for cmd in (["git", "apply", "--recount"], ["patch", "-p1"]):
        proc = subprocess.run(
            cmd, input=diff, cwd=workspace_root,
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return ""
    return f"git apply / patch failed: {(proc.stderr or '').strip()}"


def _apply_line_range(fp: Path, line_range: tuple[int, int], new_content: str) -> str:
    """按 (start, end) 行号闭区间替换为 new_content。"""
    start, end = line_range
    lines = fp.read_text(encoding="utf-8").splitlines(keepends=True)
    if start < 1 or end > len(lines) or start > end:
        return f"line_range out of bounds: {line_range} (file has {len(lines)} lines)"
    rebuilt = lines[:start - 1] + [new_content] + lines[end:]
    fp.write_text("".join(rebuilt), encoding="utf-8")
    return ""


def _apply_marker(fp: Path, old_marker: str, new_content: str, workspace_path: str) -> str:
    """old_marker 唯一匹配替换。"""
    content = fp.read_text(encoding="utf-8")
    count = content.count(old_marker)
    if count == 0:
        return f"old_marker not found in {workspace_path}"
    if count > 1:
        return f"old_marker found {count} times in {workspace_path}, not unique"
    fp.write_text(content.replace(old_marker, new_content, 1), encoding="utf-8")
    return ""

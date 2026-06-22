"""patch_applier：将 FileChange 列表应用到 workspace 源码。"""
from __future__ import annotations

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
            content = fp.read_text(encoding="utf-8")
            count = content.count(fc.old_marker)
            if count == 0:
                return ApplyResult(success=False, applied_files=applied,
                                   error=f"old_marker not found in {fc.workspace_path}")
            if count > 1:
                return ApplyResult(success=False, applied_files=applied,
                                   error=f"old_marker found {count} times in {fc.workspace_path}, not unique")
            new_content = content.replace(fc.old_marker, fc.new_content, 1)
            fp.write_text(new_content, encoding="utf-8")
        elif fc.change_type == "create":
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(fc.new_content, encoding="utf-8")
        elif fc.change_type == "delete":
            if fp.exists():
                fp.unlink()
        applied.append(fc.workspace_path)

    return ApplyResult(success=True, applied_files=applied)

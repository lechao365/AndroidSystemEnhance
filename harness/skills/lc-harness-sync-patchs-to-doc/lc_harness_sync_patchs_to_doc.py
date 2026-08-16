#!/usr/bin/env python3
"""lc_harness_sync_patchs_to_doc - code/rpi5 变动报告生成器

规则详见: harness/skills/lc-harness-sync-patchs-to-doc/SKILL.md
用法:    python3 .../lc_harness_sync_patchs_to_doc.py [--check-only] [--dry-run] [--full-diff]
退出码:  0=成功(有变动); 3=参数/环境错误; 4=无变动
"""

from __future__ import annotations

import sys
import argparse
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from harness.lib.harness_lib import (
    log_info, log_warn, log_error, step_begin, step_end,
    harness_init, harness_exit,
)
from harness.lib.paths import path as profile_path, repo_root


def _git(args: list[str], timeout: int = 300) -> str:
    """执行 git 命令，返回 stdout。失败时 log_error 并返回空字符串。"""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=repo_root(),
            timeout=timeout,
        )
        return result.stdout
    except (subprocess.SubprocessError, OSError) as e:
        log_error(f"git 失败: {' '.join(args)}: {e}")
        return ""


def _git_result(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """执行 git 命令，返回 CompletedProcess。失败时 log_error 并返回失败对象。"""
    try:
        return subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=repo_root(),
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as e:
        log_error(f"git 失败: {' '.join(args)}: {e}")
        return subprocess.CompletedProcess(args, returncode=-1, stdout="", stderr="")


def _collect_changes(patch_dir: Path, root: Path) -> list[tuple[str, str, str]]:
    """收集 tracked + untracked 变动列表，返回 (status, path1, path2) 元组列表。"""
    patch_dir_str = str(patch_dir)
    tracked = _git(["diff", "HEAD", "--name-status", "--", patch_dir_str]).strip()
    untracked_lines = _git(
        ["ls-files", "--others", "--exclude-standard", "--", patch_dir_str]
    ).strip()

    changes: list[tuple[str, str, str]] = []
    if tracked:
        for line in tracked.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                path2 = parts[2] if len(parts) > 2 else ""
                changes.append((parts[0], parts[1], path2))
    if untracked_lines:
        for line in untracked_lines.splitlines():
            changes.append(("A", line, ""))
    return changes


def _classify_group(rel: str) -> str:
    """根据相对路径分类到目录分组 key。"""
    if rel.startswith("kernel/modified/"):
        return "kernel_modified"
    elif rel.startswith("kernel/new/"):
        return "kernel_new"
    elif rel.startswith("aosp/modified/"):
        return "aosp_modified"
    elif rel.startswith("aosp/new/"):
        return "aosp_new"
    elif rel.startswith("others/"):
        return "others"
    else:
        return "root"


def _compute_numstat(stat_path: str, display_path: str, base_status: str,
                     root: Path) -> tuple[str, str]:
    """计算单个变动的增删行数，返回 (added, deleted)。"""
    numstat = _git(["diff", "HEAD", "--numstat", "--", stat_path]).strip()
    numstat_line = numstat.splitlines()[0] if numstat else ""

    if numstat_line:
        parts = numstat_line.split("\t")
        added = parts[0] if parts[0] != "-" else "0"
        deleted = parts[1] if parts[1] != "-" else "0"
    else:
        added = "0"
        deleted = "0"

    # untracked 新文件：直接统计行数
    if base_status == "A" and not numstat_line:
        fp = root / display_path
        if fp.is_file():
            try:
                added = str(fp.read_text(encoding="utf-8").count("\n"))
            except Exception as e:
                log_warn(f"读取文件行数失败: {fp}: {e}")
                added = "0"
            deleted = "0"

    return added, deleted


def _format_line(base_status: str, rel: str, added: str, deleted: str,
                 path1: str, root: Path, patch_dir: Path) -> str:
    """格式化单个变动的输出行。"""
    if base_status == "R":
        old_obj = root / path1
        try:
            old_rel = str(old_obj.relative_to(patch_dir).as_posix())
        except ValueError:
            old_rel = path1
        return f"  [R] {old_rel} → {rel}  +{added} -{deleted}"
    elif base_status == "C":
        return f"  [C] {rel}  +{added} -{deleted}"
    elif base_status == "A":
        return f"  [A] {rel}  +{added} -{deleted}"
    elif base_status == "M":
        return f"  [M] {rel}  +{added} -{deleted}"
    elif base_status == "D":
        return f"  [D] {rel}  -{deleted}"
    else:
        return f"  [{base_status}] {rel}  +{added} -{deleted}"


def _render_report(groups: dict[str, list[str]]) -> None:
    """输出分组报告到 stdout。"""
    group_order = [
        ("kernel/modified", "kernel_modified"),
        ("kernel/new", "kernel_new"),
        ("aosp/modified", "aosp_modified"),
        ("aosp/new", "aosp_new"),
        ("others", "others"),
        ("(root)", "root"),
    ]
    for label, key in group_order:
        lines = groups[key]
        if not lines:
            continue
        print("")
        print(f"--- {label}/ ---") 
        for line in lines:
            print(line)


def _render_summary(totals: dict[str, int]) -> None:
    """输出汇总统计到 stdout。"""
    total = sum(totals.values())
    parts: list[str] = []
    if totals["A"] > 0:
        parts.append(f"{totals['A']} 新增")
    if totals["M"] > 0:
        parts.append(f"{totals['M']} 修改")
    if totals["D"] > 0:
        parts.append(f"{totals['D']} 删除")
    if totals["R"] > 0:
        parts.append(f"{totals['R']} 重命名")
    if totals["other"] > 0:
        parts.append(f"{totals['other']} 其他")
    detail = ", ".join(parts)
    print("")
    print(f"总计: {total} 个文件变动 ({detail})")


def _render_full_diff(patch_dir_str: str, root: Path) -> None:
    """输出完整 diff 正文到 stdout（含 tracked diff + untracked 新文件内容）。"""
    print("")
    print("========== 完整 diff 正文（HEAD） ==========")

    diff_text = _git(["--no-pager", "diff", "HEAD", "--", patch_dir_str])
    if diff_text.strip():
        print(diff_text.rstrip())
    else:
        log_warn("无法获取 diff 正文")

    untracked_files = _git(
        ["ls-files", "--others", "--exclude-standard", "--", patch_dir_str]
    ).strip()
    if untracked_files:
        print("")
        print("--- untracked 新文件完整内容 ---")
        for f in untracked_files.splitlines():
            print("")
            print(f"+++ b/{f} (新文件)")
            try:
                content = (root / f).read_text(encoding="utf-8")
                for content_line in content.splitlines(keepends=True):
                    print(f"+{content_line}", end="")
            except Exception:
                log_warn(f"无法读取文件: {root / f}")


def _process_changes(changes: list[tuple[str, str, str]], patch_dir: Path,
                     root: Path) -> tuple[dict[str, int], dict[str, list[str]]]:
    """处理变动列表: 分类、计数、计算 numstat、格式化行。

    返回 (totals, groups)。
    """
    totals: dict[str, int] = {"A": 0, "M": 0, "D": 0, "R": 0, "other": 0}
    groups: dict[str, list[str]] = {
        "kernel_modified": [],
        "kernel_new": [],
        "aosp_modified": [],
        "aosp_new": [],
        "others": [],
        "root": [],
    }

    for status, path1, path2 in changes:
        base_status = status[0]
        display_path = path2 if base_status in ("R", "C") else path1
        stat_path = display_path

        dp_obj = root / display_path
        try:
            rel = str(dp_obj.relative_to(patch_dir).as_posix())
        except ValueError:
            rel = display_path

        group_key = _classify_group(rel)

        if base_status in totals:
            totals[base_status] += 1
        else:
            totals["other"] += 1

        added, deleted = _compute_numstat(stat_path, display_path, base_status, root)
        line = _format_line(base_status, rel, added, deleted, path1, root, patch_dir)
        groups[group_key].append(line)

    return totals, groups


def main() -> None:
    parser = argparse.ArgumentParser(description="code/rpi5 变动报告生成器")
    parser.add_argument(
        "--check-only",
        "--dry-run",
        action="store_true",
        dest="check_only",
        help="仅输出报告，不输出 AI 操作提示",
    )
    parser.add_argument(
        "--full-diff",
        action="store_true",
        help="在报告末尾追加 git diff 正文，供 AI 直接读取（零往返）",
    )
    args = parser.parse_args()

    harness_init("lc_harness_sync_patchs_to_doc")

    patch_dir = profile_path("PATCHS_DIR")
    root = repo_root()

    if not patch_dir.is_dir():
        log_error(f"code 目录不存在: {patch_dir}")
        harness_exit(3)

    head_short = _git(["rev-parse", "--short", "HEAD"]).strip() or "unknown"
    log_info(f"基准: HEAD ({head_short})")
    log_info(f"扫描: {patch_dir}/") 

    # --- 获取变动列表 ---
    changes = _collect_changes(patch_dir, root)

    if not changes:
        print("")
        log_info("无变动")
        harness_exit(4)

    # --- 统计与分组 ---
    step_begin("获取变动列表")
    step_end(True)
    step_begin("按目录分组")

    totals, groups = _process_changes(changes, patch_dir, root)

    step_end(True)
    step_begin("分组汇总输出")

    _render_report(groups)

    step_end(True)
    step_begin("汇总统计")

    _render_summary(totals)

    step_end(True)

    # --- 完整 diff（可选）---
    if args.full_diff:
        step_begin("完整 diff 正文（HEAD）")
        _render_full_diff(str(patch_dir), root)
        step_end(True)

    # --- AI 操作提示 ---
    if not args.check_only:
        print(
            "\n下一步（7 步闭环，详见 SKILL.md）：\n"
            "  ① 本报告已列出变动清单（+ --full-diff 可取完整 diff 正文）\n"
            "              ② 依据 harness/config/doc-sync-mapping.yaml"
            " 将变动分发到对应文档目录（01/02）\n"
            "  ③ 读 code/rpi5/manifest.yaml，"
            "按 source 去 ~/workspace/ 取全量源码上下文\n"
            "  ④ 用行号锚点(#L) + 符号名 + 文件名 "
            "定位受影响章节（注意形态D代码块注释盲区）\n"
            "  ⑤ 输出动作清单级方案（文档->章节->动作），用户确认后落盘\n"
            "  ⑥ 章节级增量落盘，刷新行号锚点（含盲区/区间终点/重复出现处）\n"
            "  ⑦ 一致性自检：锚点有效性 / 路径合规 / 断链 / 模板章节完整性"
        )

    harness_exit(0)


if __name__ == "__main__":
    main()

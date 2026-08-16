#!/usr/bin/env python3
"""lc_harness_sync_code_to_patchs.py — workspace → code/rpi5 全量镜像同步脚本"""

from __future__ import annotations

import sys
import os
import shutil
import subprocess
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from harness.lib.harness_lib import (
    step_begin, step_end, log_info, log_warn, log_error,
    harness_init, harness_exit,
)
from harness.lib.paths import path as profile_path, env_path as profile_env_path
from harness.config.git_workspace_util import (
    HARNESS_EXCLUDE_DIR_RE as _HARNESS_EXCLUDE_DIR_RE,
    is_excluded,
    filter_files,
    count_excluded,
)


class Counters:
    def __init__(self) -> None:
        self.ok = 0
        self.miss = 0
        self.skip = 0
        self.stale = 0
        self.prune = 0


def status_emit(status: str, label: str, detail: str = "") -> None:
    """同步状态行属数据流输出，允许裸 print。"""
    if detail:
        print(f"[{status}] {label} — {detail}", file=sys.stderr, flush=True)
    else:
        print(f"[{status}] {label}", file=sys.stderr, flush=True)


def print_ok(label: str, detail: str = "", ct: Counters | None = None) -> None:
    status_emit("OK", label, detail)
    if ct: ct.ok += 1

def print_miss(label: str, detail: str = "", ct: Counters | None = None) -> None:
    status_emit("MISS", label, detail)
    if ct: ct.miss += 1

def print_skip(label: str, detail: str = "", ct: Counters | None = None) -> None:
    status_emit("SKIP", label, detail)
    if ct: ct.skip += 1

def print_stale(label: str, detail: str = "", ct: Counters | None = None) -> None:
    status_emit("STALE", label, detail)
    if ct: ct.stale += 1

def print_prune(label: str, detail: str = "", ct: Counters | None = None) -> None:
    status_emit("PRUNE", label, detail)
    if ct: ct.prune += 1


def _git(*args: str, cwd: str | Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True,
            cwd=str(cwd), timeout=300,
        )
    except (subprocess.SubprocessError, OSError) as e:
        log_error(f"git 失败: {e}")
        p = subprocess.CompletedProcess(args, returncode=-1, stdout="", stderr="")
        return p


def git_lines(*args: str, cwd: str | Path) -> list[str]:
    r = _git(*args, cwd=cwd)
    if r.returncode != 0:
        log_error(f"git {' '.join(args)} 失败 (exit {r.returncode}): {r.stderr.strip()}")
        return []
    return [line for line in r.stdout.splitlines() if line]


def git_check(*args: str, cwd: str | Path) -> bool:
    r = _git(*args, cwd=cwd)
    return r.returncode == 0


def git_upstream_ref(cwd: str | Path) -> str | None:
    # 优先用 @{upstream}
    ups = git_lines("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", cwd=cwd)
    if ups:
        return ups[0]
    # 兜底：从 branch config 读 remote + merge
    branch = git_lines("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    if not branch or branch[0] == "HEAD":
        return None
    branch_name = branch[0]
    remote = git_lines("config", f"branch.{branch_name}.remote", cwd=cwd)
    merge = git_lines("config", f"branch.{branch_name}.merge", cwd=cwd)
    if remote and merge:
        short = merge[0].replace("refs/heads/", "") 
        return f"{remote[0]}/{short}" 
    return None


def find_upstream_base(cwd: str | Path) -> str | None:
    ups = git_upstream_ref(cwd)
    if not ups:
        return None
    base = git_lines("merge-base", "HEAD", ups, cwd=cwd)
    if base:
        return base[0]
    return None


def sync_modified_diff(ws_path: Path, base: str, files: list[str],
                       target_dir: Path, check_only: bool, label_prefix: str,
                       ct: Counters) -> None:
    for f in files:
        target = target_dir / f"{f}.diff"
        if git_check("diff", "--quiet", base, "--", f, cwd=ws_path):
            if check_only:
                print_prune(f"{label_prefix}/{f}.diff", "空diff，将清理", ct) 
            else:
                target.unlink(missing_ok=True)
                print_prune(f"{label_prefix}/{f}.diff", "空diff，已恢复原样", ct) 
            continue
        if not check_only:
            target.parent.mkdir(parents=True, exist_ok=True)
            r = _git("diff", base, "--", f, cwd=ws_path)
            target.write_text(r.stdout, encoding="utf-8")
        if target.is_file():
            print_ok(f"{label_prefix}/{f}.diff", "", ct) 
        else:
            print_miss(f"{label_prefix}/{f}.diff", "", ct) 


def sync_new_files(ws_path: Path, files: list[str],
                   target_dir: Path, check_only: bool, label_prefix: str,
                   ct: Counters) -> None:
    for f in files:
        target = target_dir / f
        src = ws_path / f
        if not check_only:
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                shutil.copy2(src, target)
        if target.is_file():
            print_ok(f"{label_prefix}/{f}", "", ct) 
        else:
            print_miss(f"{label_prefix}/{f}", "", ct) 


def sync_kernel(patch_root: Path, kernel_ws: str, check_only: bool,
                ct: Counters) -> list[str]:
    deletions: list[str] = []
    kernel_path = Path(kernel_ws)
    if not (kernel_path / ".git").is_dir():
        return deletions

    step_begin("Step 1: Kernel 同步")

    base = find_upstream_base(kernel_path)
    if not base:
        log_error("kernel: 找不到 upstream base")
        return deletions

    base_log = git_lines('log', '--oneline', '-1', base, cwd=kernel_path)
    log_info(f"Upstream base: {base_log[0] if base_log else base}")

    modified_dir = patch_root / "kernel" / "modified"
    new_dir = patch_root / "kernel" / "new"

    # Tracked deletion detection
    del_files = git_lines("diff", base, "--diff-filter=D", "--name-only", cwd=kernel_path)
    for f in del_files:
        if not is_excluded(f):
            deletions.append(f)

    log_info("--- Modified ---")
    modified = filter_files(git_lines("diff", base, "--diff-filter=M", "--name-only", cwd=kernel_path))
    sync_modified_diff(kernel_path, base, modified, modified_dir, check_only, "kernel/modified", ct)

    log_info("--- New (tracked) ---")
    new_tracked = filter_files(git_lines("diff", base, "--diff-filter=ACR", "--name-only", cwd=kernel_path))
    sync_new_files(kernel_path, new_tracked, new_dir, check_only, "kernel/new", ct)

    log_info("--- New (untracked) ---")
    untracked = filter_files(git_lines("ls-files", "--others", "--exclude-standard", cwd=kernel_path))
    sync_new_files(kernel_path, untracked, new_dir, check_only, "kernel/new", ct)

    # 编译产物汇总
    all_files = (
        git_lines("diff", base, "--name-only", cwd=kernel_path) +
        git_lines("ls-files", "--others", "--exclude-standard", cwd=kernel_path)
    )
    skip_count = count_excluded(all_files)
    if skip_count > 0:
        print_skip("kernel", f"{skip_count} 个编译产物", ct)

    step_end(True)
    return deletions


def discover_non_repo(aosp_ws: str, repo_set: set[str]) -> list[str]:
    non_repo: list[str] = []

    def _scan(prefix: str) -> None:
        search_dir = Path(aosp_ws) / prefix
        if not search_dir.is_dir():
            return
        for d in sorted(search_dir.iterdir()):
            if not d.is_dir():
                continue
            rel = str(d.relative_to(Path(aosp_ws)).as_posix())
            bn = d.name
            if bn.startswith("."):
                continue
            if _HARNESS_EXCLUDE_DIR_RE.search(bn):
                continue
            if rel in repo_set:
                continue
            if any(rel.startswith(p + "/") for p in repo_set): 
                _scan(rel + "/") 
            if d.is_symlink():
                try:
                    resolved = os.path.relpath(str(d.resolve()), str(Path(aosp_ws).resolve()))
                    if resolved in repo_set:
                        continue
                    if any(resolved.startswith(p + "/") for p in repo_set): 
                        continue
                except (ValueError, OSError) as e:
                    log_warn(f"解析符号链接失败: {d}: {e}")
            non_repo.append(rel)
            log_info(f"发现非 repo 目录: {rel}")

    _scan("")
    return non_repo


def sync_aosp(patch_root: Path, aosp_ws: str, check_only: bool,
              ct: Counters) -> list[str]:
    deletions: list[str] = []
    aosp_path = Path(aosp_ws)
    if not (aosp_path / ".repo").is_dir():
        return deletions

    step_begin("Step 2: AOSP 同步")

    # Step 0: 扫描 workspace
    step_begin("Step 0: 扫描 workspace")

    repo_list: list[str] = []
    project_list_file = aosp_path / ".repo" / "project.list"
    if project_list_file.is_file():
        repo_list = sorted(project_list_file.read_text(encoding="utf-8").splitlines())
    else:
        result = git_lines("forall", "-c", "echo $REPO_PATH", cwd=aosp_path)
        if result:
            repo_list = sorted(result)

    changed_projects: list[str] = []
    for proj in repo_list:
        proj_path = aosp_path / proj
        if not (proj_path / ".git").is_dir():
            continue
        proj_files = (
            git_lines("diff", "--name-only", cwd=proj_path) +
            git_lines("ls-files", "--others", "--exclude-standard", cwd=proj_path)
        )
        if any(f for f in proj_files if not is_excluded(f)):
            changed_projects.append(proj)

    log_info(f"有改动的 repo 项目: {len(changed_projects)}")
    non_repo_dirs = discover_non_repo(aosp_ws, set(repo_list))
    step_end(True)

    modified_dir = patch_root / "aosp" / "modified"
    new_dir = patch_root / "aosp" / "new"

    for proj_dir in changed_projects:
        proj_path = aosp_path / proj_dir
        base = find_upstream_base(proj_path)
        if not base:
            log_error(f"aosp:{proj_dir}: 找不到 upstream base")
            continue

        all_files = (
            git_lines("diff", base, "--name-only", cwd=proj_path) +
            git_lines("ls-files", "--others", "--exclude-standard", cwd=proj_path)
        )
        real_files = filter_files(all_files)
        if not real_files:
            skip_count = count_excluded(all_files)
            if skip_count > 0:
                print_skip(f"{proj_dir}", f"{skip_count} 个编译产物", ct)
            continue

        log_info(f"--- {proj_dir} ---")

        # Tracked deletion detection
        del_files = git_lines("diff", base, "--diff-filter=D", "--name-only", cwd=proj_path)
        for f in del_files:
            if not is_excluded(f):
                deletions.append(f"{proj_dir}/{f}") 

        # Modified files
        modified = filter_files(git_lines("diff", base, "--diff-filter=M", "--name-only", cwd=proj_path))
        sync_modified_diff(proj_path, base, modified, modified_dir / proj_dir,
                           check_only, f"aosp/modified/{proj_dir}", ct)

        # New tracked files
        new_tracked = filter_files(git_lines("diff", base, "--diff-filter=ACR", "--name-only", cwd=proj_path))
        sync_new_files(proj_path, new_tracked, new_dir / proj_dir,
                       check_only, f"aosp/new/{proj_dir}", ct)

        # New untracked files
        untracked = filter_files(git_lines("ls-files", "--others", "--exclude-standard", cwd=proj_path))
        sync_new_files(proj_path, untracked, new_dir / proj_dir,
                       check_only, f"aosp/new/{proj_dir}", ct)

        skip_count = count_excluded(all_files)
        if skip_count > 0:
            print_skip(f"{proj_dir}", f"{skip_count} 个编译产物", ct)

    # 非 repo 目录
    if non_repo_dirs:
        log_info("--- 非 repo 目录 ---")
        for nr_dir in non_repo_dirs:
            nr_path = aosp_path / nr_dir
            if not nr_path.is_dir():
                continue
            for f_path in sorted(nr_path.rglob("*")):
                if not f_path.is_file():
                    continue
                rel = str(f_path.relative_to(aosp_path).as_posix())
                if is_excluded(rel):
                    continue
                target = new_dir / rel
                if not check_only:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(f_path), str(target))
                if target.is_file():
                    print_ok(f"aosp/new/{rel}", "", ct)
                else:
                    print_miss(f"aosp/new/{rel}", "", ct)

    step_end(True)
    return deletions


def sync_prune(subpath: str, ws: str, strip_diff: bool,
               patch_root: Path, check_only: bool, prune: bool,
               ct: Counters) -> None:
    full_dir = patch_root / subpath
    if not full_dir.is_dir():
        return
    ws_path = Path(ws)
    for pfile in sorted(full_dir.rglob("*")):
        if not pfile.is_file():
            continue
        rel = str(pfile.relative_to(full_dir).as_posix())
        if strip_diff and rel.endswith(".diff"):
            ws_rel = rel[:-5]
        else:
            ws_rel = rel
        if not (ws_path / ws_rel).exists():
            label = f"{subpath}/{rel}" 
            if check_only:
                print_stale(label, "将删除", ct)
            elif prune:
                pfile.unlink()
                print_prune(label, "", ct)
            else:
                print_stale(label, "", ct)


def generate_manifest(patch_root: Path, check_only: bool,
                      kernel_deletions: list[str],
                      aosp_deletions: list[str]) -> None:
    manifest_path = patch_root / "manifest.yaml"

    lines: list[str] = []
    lines.append("# Auto-generated by lc_harness_sync_code_to_patchs.py — patch↔workspace 结构映射。")
    lines.append("# 禁止手动编辑。README.md（人类可读）由 AI 基于此文件维护。")
    lines.append("")

    for section, ws_root in [("kernel", "rpi5-kernel-build/common"),
                             ("aosp", "aosp")]:
        section_emitted = False
        for sub in ("modified", "new"):
            dir_path = patch_root / section / sub
            if not dir_path.is_dir():
                continue
            files: list[str] = []
            for f in sorted(dir_path.rglob("*")):
                if not f.is_file():
                    continue
                rel = str(f.relative_to(dir_path).as_posix())
                files.append(rel)
            if not files:
                continue
            if not section_emitted:
                lines.append(f"{section}:")
                section_emitted = True
            lines.append(f"  {sub}:")
            for rel in files:
                src = rel[:-5] if sub == "modified" and rel.endswith(".diff") else rel
                lines.append(f"    - patch: {section}/{sub}/{rel}") 
                lines.append(f"      source: {ws_root}/{src}") 

    # deletions 段
    if kernel_deletions or aosp_deletions:
        lines.append("deletions:")
        if kernel_deletions:
            lines.append("  kernel:")
            for f in kernel_deletions:
                lines.append(f"    - source: rpi5-kernel-build/common/{f}")
        if aosp_deletions:
            lines.append("  aosp:")
            for f in aosp_deletions:
                lines.append(f"    - source: aosp/{f}")

    # others 段
    others_dir = patch_root / "others"
    if others_dir.is_dir():
        others_files = sorted(
            str(f.relative_to(others_dir).as_posix())
            for f in others_dir.rglob("*")
            if f.is_file()
        )
        if others_files:
            lines.append("others:")
            for rel in others_files:
                lines.append(f"  - patch: others/{rel}")
                lines.append("    source: null")

    content = "\n".join(lines) + "\n"

    if not content.strip():
        log_error("manifest 内容为空，中止更新")
        return

    if manifest_path.is_file() and manifest_path.read_text(encoding="utf-8") == content:
        log_info("manifest.yaml 无变化")
        return

    if check_only:
        log_info("manifest.yaml 有变化（仅检查模式，未写入）")
        return

    manifest_path.write_text(content, encoding="utf-8")
    log_info("manifest.yaml 已更新")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="workspace → code/rpi5 全量镜像同步脚本"
    )
    parser.add_argument("--check-only", action="store_true",
                        help="仅扫描和验证，不执行归档（STALE 仅报告）")
    parser.add_argument("--no-prune", action="store_true",
                        help="仅添加/更新，不删除对齐（默认全量镜像含删除）")
    args = parser.parse_args()

    check_only = args.check_only
    prune = not args.no_prune

    harness_init("lc_harness_sync_code_to_patchs")

    # 路径变量
    patch_root = profile_path("PATCHS_DIR")
    kernel_ws = profile_env_path("KERNEL_WS")
    aosp_ws = profile_env_path("AOSP_WS")

    # Counter + deletions
    ct = Counters()
    kernel_deletions: list[str] = []
    aosp_deletions: list[str] = []

    # 前置检查
    step_begin("前置检查")
    kernel_ok = bool(kernel_ws) and (Path(kernel_ws) / ".git").is_dir()
    aosp_ok = bool(aosp_ws) and (Path(aosp_ws) / ".repo").is_dir()
    if kernel_ok:
        log_info(f"Kernel workspace: {kernel_ws}")
    if aosp_ok:
        log_info(f"AOSP workspace:   {aosp_ws}")
    if not kernel_ok and not aosp_ok:
        log_error("未找到有效的 workspace")
        harness_exit(3)
    log_info(f"模式:       {'仅检查' if check_only else '同步归档'}")
    log_info(f"Patch root: {patch_root}")
    step_end(True)

    # Step 1: Kernel 同步
    if kernel_ok:
        kernel_deletions = sync_kernel(patch_root, kernel_ws, check_only, ct)

    # Step 2: AOSP 同步
    if aosp_ok:
        aosp_deletions = sync_aosp(patch_root, aosp_ws, check_only, ct)

    # Step 3: 删除对齐
    step_begin("Step 3: 删除对齐（全量镜像）")
    if kernel_ok:
        log_info("--- Kernel ---")
        sync_prune("kernel/modified", kernel_ws, True, patch_root, check_only, prune, ct)
        sync_prune("kernel/new", kernel_ws, False, patch_root, check_only, prune, ct)
    if aosp_ok:
        log_info("--- AOSP ---")
        sync_prune("aosp/modified", aosp_ws, True, patch_root, check_only, prune, ct)
        sync_prune("aosp/new", aosp_ws, False, patch_root, check_only, prune, ct)

    # 清理空目录
    if not check_only and prune:
        for sub in ["kernel/modified", "kernel/new", "aosp/modified", "aosp/new"]:
            d = patch_root / sub
            if d.is_dir():
                for subdir in sorted(d.rglob("*"), reverse=True):
                    if subdir.is_dir() and not any(subdir.iterdir()):
                        subdir.rmdir()

    if ct.prune == 0 and ct.stale == 0:
        log_info("无删除对齐项")
    step_end(True)

    # Step 4: 更新 manifest.yaml
    step_begin("Step 4: 更新 manifest.yaml")
    generate_manifest(patch_root, check_only, kernel_deletions, aosp_deletions)
    step_end(True)

    # 汇总
    step_begin("汇总")
    log_info(f"OK={ct.ok} MISS={ct.miss} SKIP={ct.skip} STALE={ct.stale} PRUNE={ct.prune}")
    if check_only:
        log_info("本次为仅检查模式，未执行实际归档/删除操作")
    step_end(True)

    print("""
下一步：manifest.yaml 已全量重生成（含删除对齐）。README.md 由 AI 自动同步——
  1. 读取 manifest 与当前 README 文件映射表对比，识别新增/删除文件
  2. 新增文件读取对应 diff 生成"改动要点"
  3. 已删除文件（workspace 删除/恢复原样）对应行直接移除，不保留历史
  4. 直接落盘，输出更新摘要（新增 N / 删除 M / 修改要点 K）
判定：仅当存在 MISS 时停下不更新 README；PRUNE（删除对齐/空diff清理）属正常，继续更新。
""".strip(), file=sys.stderr, flush=True)

    if ct.miss > 0:
        log_warn(f"同步完成，有 {ct.miss} 个 MISS（退出码 1）")
        harness_exit(1)
    else:
        log_info("同步完成，无 MISS")
        harness_exit(0)


if __name__ == "__main__":
    main()

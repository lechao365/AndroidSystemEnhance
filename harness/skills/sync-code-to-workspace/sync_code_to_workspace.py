#!/usr/bin/env python3
"""sync_code_to_workspace - code/rpi5 -> workspace 同步

以 code/rpi5 为已知良好基线，把 workspace 中偏离 code 的部分拉回一致。

用法:
  sync_code_to_workspace.py [--plan-file <path>]             # 生成同步计划
  sync_code_to_workspace.py --apply --plan-file <path>       # 执行同步计划
  sync_code_to_workspace.py --check-only                      # 仅扫描预览
  sync_code_to_workspace.py --auto                            # 单进程闭环（生成→全选→执行→校验）
"""

import sys, os, subprocess, shutil, tempfile, argparse, atexit
from pathlib import Path
from datetime import datetime
from functools import lru_cache

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from harness.lib.harness_lib import (
    log_info, log_warn, log_error, step_begin, step_end,
    harness_init, harness_exit,
)
from harness.lib.paths import (
    path as profile_path, env_path as core_env_path,
    log_dir as core_log_dir,
)
from harness.config.git_workspace_util import is_excluded, is_excluded_dir

# ── 路径惰性求值（首次调用时求值并缓存） ──────────────────
# 此前 PATCH_ROOT/KERNEL_WS 等在 import 期求值，产生副作用且无法适配
# 运行时环境变化。改为 lru_cache 惰性函数，首次调用时求值并缓存。


@lru_cache(maxsize=None)
def _patch_root() -> Path:
    """code 根目录（profile 层 PATCHS_DIR）。"""
    return profile_path("PATCHS_DIR")


@lru_cache(maxsize=None)
def _kernel_ws() -> str:
    """kernel workspace 路径（KERNEL_WS 环境变量覆盖 paths.conf）。"""
    return core_env_path("KERNEL_WS") or ""


@lru_cache(maxsize=None)
def _aosp_ws() -> str:
    """AOSP workspace 路径（AOSP_WS 环境变量覆盖 paths.conf）。"""
    return core_env_path("AOSP_WS") or ""


# ── 排除正则（统一使用 git_workspace_util 共享模块） ──────


# ── artifacts 路径 ────────────────────────


@lru_cache(maxsize=None)
def _artifacts_dir() -> Path:
    """同步脚本 artifacts 目录: harness/log/<script>/artifacts/。"""
    d = core_log_dir() / "sync-code-to-workspace" / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _artifact_path(suffix: str) -> str:
    """artifacts 目录下带时间戳的文件路径。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(_artifacts_dir() / f"{ts}-{suffix}")


_tmp_files: list[str] = []


def _cleanup():
    for f in _tmp_files:
        try:
            os.unlink(f)
        except OSError as e:
            log_warn(f"临时文件清理失败: {f}: {e}")


atexit.register(_cleanup)


def _tmp_file(suffix: str = "") -> str:
    """创建 /tmp 临时文件（仅用于扫描期 scratch，非交付产物）。"""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    _tmp_files.append(path)
    return path


# ── git helpers（timeout + 失败日志） ────────────────────────────


def _git_run(args: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess:
    """统一 git 执行入口，带 timeout 与异常兜底。

    timeout/异常时返回 returncode=-1 的 CompletedProcess，避免无限阻塞。
    """
    cmd = ["git"] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(cwd), timeout=timeout)
    except subprocess.TimeoutExpired:
        log_error(f"git {' '.join(args)} 超时({timeout}s)")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=f"超时({timeout}s)")
    except (subprocess.SubprocessError, OSError) as e:
        log_error(f"git {' '.join(args)} 执行异常: {e}")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=str(e))


def _git_lines(*args: str, cwd: str | Path = ".", timeout: int = 300) -> list[str]:
    """git 输出按行返回；失败时 log_error 记录 stderr 并返回空列表。"""
    r = _git_run(list(args), cwd, timeout)
    if r.returncode != 0:
        log_error(f"git {' '.join(args)} 失败: {r.stderr.strip()}")
        return []
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def _git_check(*args: str, cwd: str | Path = ".", timeout: int = 300) -> bool:
    """git 命令是否成功（returncode == 0）。"""
    return _git_run(list(args), cwd, timeout).returncode == 0


def _find_upstream_base(cwd: str | Path = ".") -> str | None:
    ups_ref = _git_lines("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", cwd=cwd)
    if not ups_ref:
        return None
    base = _git_lines("merge-base", "HEAD", ups_ref[0], cwd=cwd)
    return base[0] if base else None


# ── exclusion ────────────────────────────────────────────────────────


def _is_excluded(f: str) -> bool:
    if is_excluded(f):
        return True
    bn = f.split("/", 1)[0]
    if is_excluded_dir(bn):
        return True
    return False


# ── project parsing ──────────────────────────────────────────────────


def _parse_proj(proj: str) -> str | None:
    if proj == "kernel":
        return _kernel_ws()
    if proj == "aosp" or proj.startswith("aosp:"):
        return _aosp_ws()
    return None


def _resolve_proj_cwd(proj: str) -> str | None:
    """项目实际 git 工作目录（aosp:<scope> 为 aosp 根下对应子目录）。"""
    ws = _parse_proj(proj)
    if not ws:
        return None
    if proj.startswith("aosp:"):
        scope = proj.split(":", 1)[1]
        return os.path.join(ws, scope)
    return ws


def _resolve_workspace_target(proj: str, rel: str) -> str | None:
    """解析 workspace 目标路径。repo 项目 rel 相对项目根；非 repo 项目 rel 相对 aosp 根（含 scope 前缀）。"""
    if proj.startswith("aosp:"):
        scope = proj.split(":", 1)[1]
        if rel.startswith(scope + os.sep):
            return os.path.join(_aosp_ws(), rel)
    ws = _resolve_proj_cwd(proj)
    if not ws:
        return None
    return os.path.join(ws, rel)


# ── diff normalization（显式 encoding） ─────────────────────────


def _diff_normalized(path1: str, path2: str) -> bool:
    try:
        lines1 = [l for l in Path(path1).read_text(encoding="utf-8").splitlines() if not l.startswith("index ")]
        lines2 = [l for l in Path(path2).read_text(encoding="utf-8").splitlines() if not l.startswith("index ")]
        return lines1 == lines2
    except (OSError, IOError) as e:
        log_warn(f"diff normalization 文件读取失败: {path1} vs {path2}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════
# Kernel scan
# ═══════════════════════════════════════════════════════════════════════


def _coverage_kernel() -> set[str]:
    covered: set[str] = set()
    modified_dir = Path(_patch_root()) / "kernel" / "modified"
    if modified_dir.is_dir():
        for dfile in modified_dir.rglob("*.diff"):
            rel = str(dfile.relative_to(modified_dir).as_posix())
            rel = rel.rsplit(".diff", 1)[0]
            covered.add(rel)
    new_dir = Path(_patch_root()) / "kernel" / "new"
    if new_dir.is_dir():
        for f in new_dir.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(new_dir).as_posix())
                covered.add(rel)
    return covered


def _scan_kernel_modified(out: str) -> tuple[int, int]:
    """扫描 kernel modified 差异。返回 (match_count, error_count)。"""
    modified_dir = Path(_patch_root()) / "kernel" / "modified"
    if not modified_dir.is_dir():
        return (0, 0)
    base = _find_upstream_base(cwd=_kernel_ws())
    if not base:
        log_warn("kernel: 无法确定 upstream base（无 upstream 或 detached HEAD）")
        return (0, 1)
    g_match = 0
    errors = 0
    for dfile in sorted(modified_dir.rglob("*.diff")):
        rel = str(dfile.relative_to(modified_dir).as_posix())
        rel = rel.rsplit(".diff", 1)[0]
        tmp = _tmp_file(".diff")
        r = _git_run(["diff", base, "--", rel], cwd=_kernel_ws())
        if r.returncode != 0:
            log_error(f"kernel: git diff 失败: {rel} ({r.stderr.strip()})")
            errors += 1
            continue
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(r.stdout)
        if os.path.getsize(tmp) == 0:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"+\tMODIFIED-DIVERGED\tkernel\t{rel}\tcheckout\tworkspace 已恢复 upstream，缺失 code 定制\n")
        elif _diff_normalized(tmp, str(dfile)):
            g_match += 1
        else:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"+\tMODIFIED-DIVERGED\tkernel\t{rel}\tcheckout\tworkspace diff 与 code 不一致\n")
    return (g_match, errors)


def _scan_kernel_new(out: str) -> tuple[int, int]:
    """扫描 kernel new 差异。返回 (match_count, error_count)。"""
    new_dir = Path(_patch_root()) / "kernel" / "new"
    if not new_dir.is_dir():
        return (0, 0)
    g_match = 0
    for pfile in sorted(new_dir.rglob("*")):
        if not pfile.is_file():
            continue
        rel = str(pfile.relative_to(new_dir).as_posix())
        src = Path(_kernel_ws()) / rel
        if not src.is_file():
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"+\tNEW-MISMATCH\tkernel\t{rel}\trestore\tworkspace 缺失\n")
        elif src.read_bytes() != pfile.read_bytes():
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"+\tNEW-MISMATCH\tkernel\t{rel}\trestore\t内容与 code 不一致\n")
        else:
            g_match += 1
    return (g_match, 0)


def _scan_extra_kernel(out: str) -> tuple[int, int]:
    """扫描 kernel 未归档改动。返回 (match_count, error_count)。"""
    base = _find_upstream_base(cwd=_kernel_ws())
    if not base:
        log_warn("kernel: 无法确定 upstream base")
        return (0, 1)
    covered = _coverage_kernel()
    ws_changes: set[str] = set()
    ws_changes.update(_git_lines("diff", base, "--name-only", cwd=_kernel_ws()))
    ws_changes.update(_git_lines("ls-files", "--others", "--exclude-standard", cwd=_kernel_ws()))
    extra = sorted(ws_changes - covered)
    count = 0
    for f in extra:
        if not f or _is_excluded(f):
            continue
        if _git_check("cat-file", "-e", f"{base}:{f}", cwd=_kernel_ws()):
            with open(out, "a", encoding="utf-8") as of:
                of.write(f"+\tEXTRA-MODIFIED\tkernel\t{f}\tsync\t未归档的 upstream 文件改动\n")
            count += 1
        elif _git_check("ls-files", "--error-unmatch", f, cwd=_kernel_ws()):
            with open(out, "a", encoding="utf-8") as of:
                of.write(f"+\tEXTRA-NEW-TRACKED\tkernel\t{f}\tsync\t未归档 tracked 新文件\n")
            count += 1
        else:
            with open(out, "a", encoding="utf-8") as of:
                of.write(f"+\tEXTRA-NEW-UNTRACKED\tkernel\t{f}\tsync\t未归档 untracked 新文件\n")
            count += 1
    return (count, 0)


# ═══════════════════════════════════════════════════════════════════════
# AOSP scan
# ═══════════════════════════════════════════════════════════════════════


def _coverage_aosp_project(proj: str) -> set[str]:
    covered: set[str] = set()
    modified_dir = Path(_patch_root()) / "aosp" / "modified" / proj
    if modified_dir.is_dir():
        for dfile in modified_dir.rglob("*.diff"):
            rel = str(dfile.relative_to(modified_dir).as_posix())
            rel = rel.rsplit(".diff", 1)[0]
            covered.add(rel)
    new_dir = Path(_patch_root()) / "aosp" / "new" / proj
    if new_dir.is_dir():
        for f in new_dir.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(new_dir).as_posix())
                covered.add(rel)
    return covered


def _scan_aosp_modified(out: str) -> tuple[int, int]:
    """扫描 aosp modified 差异。返回 (match_count, error_count)。"""
    modified_root = Path(_patch_root()) / "aosp" / "modified"
    if not modified_root.is_dir():
        return (0, 0)
    proj_list = Path(_aosp_ws()) / ".repo" / "project.list"
    if not proj_list.is_file():
        log_error(f"aosp: .repo/project.list 不存在: {proj_list}，无法扫描 aosp modified")
        return (0, 1)
    g_match = 0
    errors = 0
    projects = [l.strip() for l in proj_list.read_text(encoding="utf-8").splitlines() if l.strip()]
    for proj in projects:
        proj_dir = modified_root / proj
        if not proj_dir.is_dir():
            continue
        proj_ws = Path(_aosp_ws()) / proj
        if not (proj_ws / ".git").is_dir():
            continue
        base = _find_upstream_base(cwd=proj_ws)
        if not base:
            log_warn(f"aosp:{proj}: 无法确定 upstream base")
            errors += 1
            continue
        for dfile in sorted(proj_dir.rglob("*.diff")):
            rel = str(dfile.relative_to(proj_dir).as_posix())
            rel = rel.rsplit(".diff", 1)[0]
            tmp = _tmp_file(".diff")
            r = _git_run(["diff", base, "--", rel], cwd=proj_ws)
            if r.returncode != 0:
                log_error(f"aosp:{proj}: git diff 失败: {rel} ({r.stderr.strip()})")
                errors += 1
                continue
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(r.stdout)
            if os.path.getsize(tmp) == 0:
                with open(out, "a", encoding="utf-8") as f:
                    f.write(f"+\tMODIFIED-DIVERGED\taosp:{proj}\t{rel}\tcheckout\tworkspace 已恢复 upstream，缺失 code 定制\n")
            elif _diff_normalized(tmp, str(dfile)):
                g_match += 1
            else:
                with open(out, "a", encoding="utf-8") as f:
                    f.write(f"+\tMODIFIED-DIVERGED\taosp:{proj}\t{rel}\tcheckout\tworkspace diff 与 code 不一致\n")
    return (g_match, errors)


def _scan_aosp_new(out: str) -> tuple[int, int]:
    """扫描 aosp new 差异。返回 (match_count, error_count)。"""
    new_root = Path(_patch_root()) / "aosp" / "new"
    if not new_root.is_dir():
        return (0, 0)
    proj_list = Path(_aosp_ws()) / ".repo" / "project.list"
    if not proj_list.is_file():
        log_error(f"aosp: .repo/project.list 不存在: {proj_list}，无法扫描 aosp new")
        return (0, 1)
    g_match = 0
    projects = [l.strip() for l in proj_list.read_text(encoding="utf-8").splitlines() if l.strip()]

    for proj in projects:
        proj_dir = new_root / proj
        if not proj_dir.is_dir():
            continue
        for pfile in sorted(proj_dir.rglob("*")):
            if not pfile.is_file():
                continue
            rel = str(pfile.relative_to(proj_dir).as_posix())
            src = Path(_aosp_ws()) / proj / rel
            if not src.is_file():
                with open(out, "a", encoding="utf-8") as f:
                    f.write(f"+\tNEW-MISMATCH\taosp:{proj}\t{rel}\trestore\tworkspace 缺失\n")
            elif src.read_bytes() != pfile.read_bytes():
                with open(out, "a", encoding="utf-8") as f:
                    f.write(f"+\tNEW-MISMATCH\taosp:{proj}\t{rel}\trestore\t内容与 code 不一致\n")
            else:
                g_match += 1

    # 非 repo 目录的 new
    all_new: set[str] = set()
    for p in new_root.rglob("*"):
        if p.is_file():
            all_new.add(str(p.relative_to(new_root).as_posix()))
    repo_new: set[str] = set()
    for proj in projects:
        pdir = new_root / proj
        if pdir.is_dir():
            for p in pdir.rglob("*"):
                if p.is_file():
                    repo_new.add(str(p.relative_to(new_root).as_posix()))
    non_repo_new = sorted(all_new - repo_new)
    for rel in non_repo_new:
        src = Path(_aosp_ws()) / rel
        pfile = new_root / rel
        if not src.is_file():
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"+\tNEW-MISMATCH\taosp\t{rel}\trestore\tworkspace 缺失（非 repo）\n")
        elif src.read_bytes() != pfile.read_bytes():
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"+\tNEW-MISMATCH\taosp\t{rel}\trestore\t内容不一致（非 repo）\n")
        else:
            g_match += 1
    return (g_match, 0)


def _scan_extra_aosp(out: str) -> tuple[int, int]:
    """扫描 aosp 未归档改动。返回 (match_count, error_count)。"""
    proj_list = Path(_aosp_ws()) / ".repo" / "project.list"
    if not proj_list.is_file():
        log_error(f"aosp: .repo/project.list 不存在: {proj_list}，无法扫描 aosp extra")
        return (0, 1)
    projects = [l.strip() for l in proj_list.read_text(encoding="utf-8").splitlines() if l.strip()]

    errors = 0
    for proj in projects:
        proj_ws = Path(_aosp_ws()) / proj
        if not (proj_ws / ".git").is_dir():
            continue
        r = _git_run(["status", "--porcelain"], proj_ws)
        if r.returncode != 0:
            log_error(f"aosp:{proj}: git status --porcelain 失败: {r.stderr.strip()}")
            errors += 1
            continue
        if not r.stdout.strip():
            continue
        base = _find_upstream_base(cwd=proj_ws)
        if not base:
            errors += 1
            continue
        covered = _coverage_aosp_project(proj)
        ws_changes: set[str] = set()
        ws_changes.update(_git_lines("diff", base, "--name-only", cwd=proj_ws))
        ws_changes.update(_git_lines("ls-files", "--others", "--exclude-standard", cwd=proj_ws))
        extra = sorted(ws_changes - covered)
        for f in extra:
            if not f or _is_excluded(f):
                continue
            if _git_check("cat-file", "-e", f"{base}:{f}", cwd=proj_ws):
                with open(out, "a", encoding="utf-8") as of:
                    of.write(f"+\tEXTRA-MODIFIED\taosp:{proj}\t{f}\tsync\t未归档的 upstream 文件改动\n")
            elif _git_check("ls-files", "--error-unmatch", f, cwd=proj_ws):
                with open(out, "a", encoding="utf-8") as of:
                    of.write(f"+\tEXTRA-NEW-TRACKED\taosp:{proj}\t{f}\tdelete\t未归档 tracked 新文件（code 已删，删除对齐）\n")
            else:
                with open(out, "a", encoding="utf-8") as of:
                    of.write(f"+\tEXTRA-NEW-UNTRACKED\taosp:{proj}\t{f}\tdelete\t未归档 untracked 新文件（code 已删，删除对齐）\n")

    _scan_extra_aosp_non_repo(out)
    return (0, errors)


def _scan_extra_aosp_non_repo(out: str):
    new_root = Path(_patch_root()) / "aosp" / "new"
    cov_all: set[str] = set()
    if new_root.is_dir():
        for p in new_root.rglob("*"):
            if p.is_file():
                cov_all.add(str(p.relative_to(new_root).as_posix()))

    proj_list = Path(_aosp_ws()) / ".repo" / "project.list"
    if not proj_list.is_file():
        return
    projects = [l.strip() for l in proj_list.read_text(encoding="utf-8").splitlines() if l.strip()]
    top_projects = {p.split("/", 1)[0] for p in projects}

    for d in sorted(Path(_aosp_ws()).iterdir()):
        if not d.is_dir():
            continue
        bn = d.name
        if bn.startswith("."):
            continue
        if is_excluded_dir(bn):
            continue
        if bn in top_projects:
            continue
        if any(p.startswith(bn + "/") for p in projects): 
            continue
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            rel = str(f.relative_to(Path(_aosp_ws())).as_posix())
            if _is_excluded(rel):
                continue
            if rel in cov_all:
                continue
            with open(out, "a", encoding="utf-8") as of:
                of.write(f"+\tEXTRA-NEW-UNTRACKED\taosp:{bn}\t{rel}\tsync\t非 repo 目录未归档文件\n")


# ═══════════════════════════════════════════════════════════════════════
# Plan generation
# ═══════════════════════════════════════════════════════════════════════


def _gen_plan(out: str) -> int:
    """生成同步计划。扫描失败时返回 1（scan_rc 保护生效）。"""
    g_match_modified = 0
    g_match_new = 0
    scan_rc = 0

    tmp = _tmp_file(".tsv")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# SYNC-PLAN generated\n")
        f.write("# 格式: <标记>\\t<类别>\\t<项目>\\t<相对路径>\\t<动作>\\t<差异摘要>\n")
        f.write("# 标记: + = 选中执行, - = 不执行\n")
        f.write("# 类别: MODIFIED-DIVERGED | NEW-MISMATCH | EXTRA-MODIFIED | EXTRA-NEW-TRACKED | EXTRA-NEW-UNTRACKED\n")
        f.write("# 动作: checkout | checkout-only | restore | sync | delete | skip | stash-hint\n")
        f.write("\n")

    kernel_ok = bool(_kernel_ws()) and (Path(_kernel_ws()) / ".git").is_dir()
    aosp_ok = bool(_aosp_ws()) and (Path(_aosp_ws()) / ".repo").is_dir()

    if kernel_ok:
        log_info("扫描 kernel")
        m, e = _scan_kernel_modified(tmp)
        g_match_modified += m
        scan_rc += e
        n, e = _scan_kernel_new(tmp)
        g_match_new += n
        scan_rc += e
        _, e = _scan_extra_kernel(tmp)
        scan_rc += e

    if aosp_ok:
        log_info("扫描 aosp")
        m, e = _scan_aosp_modified(tmp)
        g_match_modified += m
        scan_rc += e
        n, e = _scan_aosp_new(tmp)
        g_match_new += n
        scan_rc += e
        _, e = _scan_extra_aosp(tmp)
        scan_rc += e

    if scan_rc:
        log_error(f"扫描阶段失败（{scan_rc} 个错误），不输出 plan")
        return 1

    shutil.copy2(tmp, out)

    total = sum(1 for l in Path(out).read_text(encoding="utf-8").splitlines() if l and l[0] in "+-")
    log_info("扫描完成")
    log_info(f"MODIFIED-MATCH: {g_match_modified} 个文件已是 code 状态（不列入 plan）")
    log_info(f"NEW-MATCH: {g_match_new} 个文件已是 code 状态（不列入 plan）")
    log_info(f"需确认条目: {total} 个（详见 plan 文件）")
    log_info(f"Plan 文件: {out}")
    return 0


def _gen_plan_silent(out: str) -> int:
    """静默生成 plan，返回扫描错误计数（供 verify 判定；>0 表示校验结果不可信）。"""
    with open(out, "w", encoding="utf-8"):
        pass

    kernel_ok = bool(_kernel_ws()) and (Path(_kernel_ws()) / ".git").is_dir()
    aosp_ok = bool(_aosp_ws()) and (Path(_aosp_ws()) / ".repo").is_dir()

    scan_rc = 0
    if kernel_ok:
        _, e = _scan_kernel_modified(out)
        scan_rc += e
        _, e = _scan_kernel_new(out)
        scan_rc += e
        _, e = _scan_extra_kernel(out)
        scan_rc += e
    if aosp_ok:
        _, e = _scan_aosp_modified(out)
        scan_rc += e
        _, e = _scan_aosp_new(out)
        scan_rc += e
        _, e = _scan_extra_aosp(out)
        scan_rc += e
    return scan_rc


# ═══════════════════════════════════════════════════════════════════════
# Apply
# ═══════════════════════════════════════════════════════════════════════


def _do_checkout_patch(proj: str, rel: str) -> bool:
    ws = _resolve_proj_cwd(proj)
    if not ws:
        log_error(f"do_checkout_patch: 未知 proj={proj}")
        return False
    if proj == "kernel":
        diff_file = Path(_patch_root()) / "kernel" / "modified" / f"{rel}.diff"
    elif proj.startswith("aosp:"):
        scope = proj.split(":", 1)[1]
        diff_file = Path(_patch_root()) / "aosp" / "modified" / scope / f"{rel}.diff"
    else:
        log_error(f"do_checkout_patch 不支持 proj={proj}")
        return False
    if not diff_file.is_file():
        log_error(f"code diff 不存在: {diff_file}")
        return False
    base = _find_upstream_base(cwd=ws)
    if not base:
        log_error(f"{proj}: 无法确定 upstream base")
        return False
    r2 = _git_run(["checkout", base, "--", rel], cwd=ws)
    if r2.returncode != 0:
        log_error(f"checkout 失败: {rel} ({r2.stderr.strip()})")
        return False
    r1 = _git_run(["apply", "--check", str(diff_file)], cwd=ws)
    if r1.returncode != 0:
        log_error(f"BROKEN-DIFF: {diff_file} 无法应用到 upstream（文件已回退 base）")
        return False
    r3 = _git_run(["apply", str(diff_file)], cwd=ws)
    if r3.returncode != 0:
        log_error(f"git apply 失败: {diff_file} ({r3.stderr.strip()})")
        return False
    return True


def _do_checkout_only(proj: str, rel: str) -> bool:
    ws = _resolve_proj_cwd(proj)
    if not ws:
        return False
    base = _find_upstream_base(cwd=ws)
    if not base:
        log_error(f"{proj}: 无法确定 upstream base")
        return False
    r = _git_run(["checkout", base, "--", rel], cwd=ws)
    if r.returncode != 0:
        log_error(f"checkout 失败: {rel} ({r.stderr.strip()})")
        return False
    return True


def _do_restore(proj: str, rel: str) -> bool:
    ws = _resolve_proj_cwd(proj)
    if not ws:
        return False
    if proj == "kernel":
        pfile = Path(_patch_root()) / "kernel" / "new" / rel
    elif proj == "aosp":
        pfile = Path(_patch_root()) / "aosp" / "new" / rel
    elif proj.startswith("aosp:"):
        scope = proj.split(":", 1)[1]
        pfile = Path(_patch_root()) / "aosp" / "new" / scope / rel
    else:
        log_error(f"do_restore 不支持 proj={proj}")
        return False
    if not pfile.is_file():
        log_error(f"code 源文件不存在: {pfile}")
        return False
    target = Path(ws) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pfile, target)
    return True


def _do_sync_extra(proj: str, rel: str, category: str) -> bool:
    ws = _resolve_proj_cwd(proj)
    if not ws:
        return False
    if category == "EXTRA-MODIFIED":
        base = _find_upstream_base(cwd=ws)
        if not base:
            log_error(f"{proj}: 无法确定 upstream base")
            return False
        r = _git_run(["checkout", base, "--", rel], cwd=ws)
        if r.returncode != 0:
            log_error(f"checkout 失败: {rel} ({r.stderr.strip()})")
            return False
        return True
    elif category == "EXTRA-NEW-TRACKED":
        # tracked 新文件须同时清理 index 与工作树。不能走 `git checkout base -- f`：
        # base 中不存在该 pathspec 时 git 会报 "did not match" 而失败。
        r = _git_run(["rm", "-f", "--", rel], cwd=ws)
        if r.returncode != 0:
            log_error(f"git rm 失败: {rel} ({r.stderr.strip()})")
            return False
        return True
    elif category in ("EXTRA-NEW-UNTRACKED", "EXTRA-DELETE"):
        target = _resolve_workspace_target(proj, rel)
        if not target:
            log_error(f"无法解析路径: {proj}/{rel}")
            return False
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.unlink(target)
        except OSError as e:
            log_error(f"rm 失败: {target} ({e})")
            return False
        return True
    else:
        log_error(f"未知 EXTRA 类别: {category}")
        return False


def _select_all(plan_file: str) -> None:
    """把 plan 中所有 '-' 标记行改为 '+'（全选），供 --auto 模式使用。

    注释行、空行与已选中的 '+' 行保持原样，避免产生假条目。
    """
    p = Path(plan_file)
    lines = p.read_text(encoding="utf-8").splitlines()
    out = [ln if (not ln or ln.startswith(("#", "+"))) else ("+" + ln[1:]) for ln in lines]
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def _apply_plan(plan: str) -> bool:
    if not os.path.isfile(plan):
        log_error(f"plan 文件不存在: {plan}")
        return False
    lines = Path(plan).read_text(encoding="utf-8").splitlines()
    selected = sum(1 for l in lines if l and l[0] == "+")
    if selected == 0:
        log_info("plan 中无选中条目（+ 标记），无需执行")
        return True

    log_info(f"执行同步计划 ({selected} 条)")
    log_info(f"Plan 文件: {plan}")
    log_warn("如 workspace 有 staged 改动（git index），checkout 可能受影响；建议先 git stash")

    applied = 0
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if not line.startswith("+"):
            continue
        parts = line.split("\t", 5)
        if len(parts) < 6:
            log_warn(f"plan 行格式异常，跳过: {line[:80]}")
            continue
        mark, category, proj, rel, action, summary = parts
        rc = True
        if action == "checkout":
            log_info(f"  [CHECKOUT] {proj}:{rel}")
            rc = _do_checkout_patch(proj, rel)
        elif action == "checkout-only":
            log_info(f"  [CHECKOUT-ONLY] {proj}:{rel}")
            rc = _do_checkout_only(proj, rel)
        elif action == "restore":
            log_info(f"  [RESTORE] {proj}:{rel}")
            rc = _do_restore(proj, rel)
        elif action == "sync":
            log_info(f"  [SYNC] {category} {proj}:{rel}")
            rc = _do_sync_extra(proj, rel, category)
        elif action == "delete":
            log_info(f"  [DELETE] {proj}:{rel}")
            rc = _do_sync_extra(proj, rel, "EXTRA-DELETE")
        elif action in ("skip", "stash-hint"):
            continue
        else:
            log_warn(f"  未知动作 '{action}'，跳过: {proj}:{rel}")
            continue
        if rc:
            applied += 1
        else:
            log_error(f"应用失败: {action} {proj}:{rel}")
            return False

    log_info("执行完成")
    log_info(f"已执行: {applied} 条")
    log_info(f"APPLY 结果: applied={applied}, plan={plan}")
    return True


# ═══════════════════════════════════════════════════════════════════════
# Verify
# ═══════════════════════════════════════════════════════════════════════


def _verify_after_apply(orig_plan: str) -> bool:
    log_info("落盘校验（全量重跑）")
    new_plan = _tmp_file("verify-plan.tsv")
    scan_rc = _gen_plan_silent(new_plan)
    if scan_rc:
        log_error(f"落盘校验扫描失败（{scan_rc} 个错误），校验结果不可信，判定失败")
        return False

    orig_lines = Path(orig_plan).read_text(encoding="utf-8").splitlines()
    new_lines = Path(new_plan).read_text(encoding="utf-8").splitlines() if os.path.isfile(new_plan) else []

    orig_exec: set[str] = set()
    orig_skip: set[str] = set()
    for l in orig_lines:
        if not l or l.startswith("#"):
            continue
        parts = l.split("\t")
        if len(parts) < 4:
            continue
        key = f"{parts[2]}\t{parts[3]}"
        if l[0] == "+":
            orig_exec.add(key)
        elif l[0] == "-":
            orig_skip.add(key)

    new_diverged: set[str] = set()
    for l in new_lines:
        if not l or l.startswith("#"):
            continue
        parts = l.split("\t")
        if len(parts) < 4:
            continue
        key = f"{parts[2]}\t{parts[3]}"
        new_diverged.add(key)

    verify_out = _artifact_path("verify.tsv")
    fixed = sorted(orig_exec - new_diverged)
    kept = sorted(orig_skip & new_diverged)
    residual = sorted(orig_exec & new_diverged)
    newdiff = sorted(new_diverged - orig_exec - orig_skip)

    with open(verify_out, "w", encoding="utf-8") as f:
        f.write("# VERIFY generated\n")
        f.write("# FIXED    = 原执行条目现已 MATCH（同步生效）\n")
        f.write("# KEPT     = 原 skip 条目仍偏离（用户主动保留，不算失败）\n")
        f.write("# RESIDUAL = 原执行条目仍偏离（同步未生效，真正失败）\n")
        f.write("# NEW-DIFF = 新出现的偏离（需排查）\n\n")
        for k in fixed:
            f.write(f"FIXED\t{k}\n")
        for k in kept:
            f.write(f"KEPT\t{k}\n")
        for k in residual:
            f.write(f"RESIDUAL\t{k}\n")
        for k in newdiff:
            f.write(f"NEW-DIFF\t{k}\n")

    log_info(f"FIXED:    {len(fixed)}")
    log_info(f"KEPT:     {len(kept)}（用户主动保留，不算失败）")
    log_info(f"RESIDUAL: {len(residual)}")
    log_info(f"NEW-DIFF: {len(newdiff)}")
    log_info(f"校验文件: {verify_out}")
    log_info(f"VERIFY 结果: fixed={len(fixed)}, kept={len(kept)}, "
             f"residual={len(residual)}, new_diff={len(newdiff)}, verify_file={verify_out}")

    log_info(f"校验文件归档: {verify_out}")

    if residual or newdiff:
        log_error(f"校验失败：有 RESIDUAL({len(residual)}) 或 NEW-DIFF({len(newdiff)})")
        return False
    log_info("校验通过")
    return True


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════


def _mark_stage(name):
    """验证阶段自动打点：cdp_timing.py mark（batch 识别：CDP_BATCH_ID 环境变量
    > log 目录唯一 timings 文件；均缺时静默跳过返 0，失败不阻断口径）。"""
    timing = (Path(__file__).resolve().parents[1] / "cross-device"
              / "lib" / "python" / "cdp_timing.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(timing.parent) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        r = subprocess.run([sys.executable, str(timing), "mark", "--name", name],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        log_warn(f"打点 {name} 失败（不阻断）: {e}")
        return
    if r.returncode != 0:
        log_warn(f"打点 {name} 失败（不阻断）: {r.stderr.strip()}")


def main():
    harness_init("sync_code_to_workspace")

    parser = argparse.ArgumentParser(
        description="code/rpi5 -> workspace 同步",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--plan-file", help="同步计划文件路径")
    parser.add_argument("--apply", action="store_true", help="执行同步计划")
    parser.add_argument("--check-only", action="store_true", help="仅扫描预览，不生成 plan 文件")
    parser.add_argument("--auto", action="store_true",
                        help="自动模式：生成计划→全选→执行→落盘校验单进程闭环；"
                             "plan 为空视为成功 exit 0（workspace-verify 内部使用）")
    args = parser.parse_args()

    if args.apply and not args.plan_file:
        log_error("--apply 模式必须配合 --plan-file <path>")
        harness_exit(3)

    kernel_ok = bool(_kernel_ws()) and (Path(_kernel_ws()) / ".git").is_dir()
    aosp_ok = bool(_aosp_ws()) and (Path(_aosp_ws()) / ".repo").is_dir()

    if not kernel_ok and not aosp_ok:
        log_error("未找到有效的 workspace（检查 KERNEL_WS/AOSP_WS 环境变量）")
        harness_exit(3)
    if kernel_ok:
        log_info(f"Kernel workspace: {_kernel_ws()}")
    if aosp_ok:
        log_info(f"AOSP workspace:   {_aosp_ws()}")
    log_info(f"Patch root: {_patch_root()}")

    # 前置：真相源为 code 仓 dev/main HEAD（spec §3.1：交互模式与 --auto 一致放宽，
    # 不再要求 promoted baseline；恢复真相源约束由 SRC-004 在规则层表达）
    if not _git_check("rev-parse", "--verify", "HEAD", cwd=str(_patch_root())):
        log_error("code 仓无 HEAD，无法以 dev/main 为真相源")
        harness_exit(3)

    if args.auto:
        plan_file = args.plan_file or _artifact_path("auto_plan.tsv")
        step_begin("阶段 1: 生成同步计划")
        rc = _gen_plan(plan_file)
        step_end(rc == 0)
        if rc != 0:
            log_error("auto: 计划生成失败")
            harness_exit(3)
        _select_all(plan_file)
        plan_lines = [l for l in Path(plan_file).read_text(encoding="utf-8").splitlines()
                      if l and l[0] in "+-"]
        if not plan_lines:
            log_info("auto: plan 为空，code 与 workspace 一致，无需同步")
            _mark_stage("verify_sync")
            harness_exit(0)
        step_begin("阶段 2: 执行同步计划")
        if not _apply_plan(plan_file):
            step_end(False)
            log_error("auto: apply 失败")
            harness_exit(1)
        step_end(True)
        step_begin("阶段 3: 落盘校验")
        ok = _verify_after_apply(plan_file)
        step_end(ok)
        # 脚本自动打点同步段（--auto 闭环完成即记；失败不阻断口径）
        _mark_stage("verify_sync")
        harness_exit(0 if ok else 1)

    mode = "apply" if args.apply else ("check-only" if args.check_only else "plan")

    if mode == "plan":
        plan_file = args.plan_file or _artifact_path("plan.tsv")
        step_begin("阶段 1: 生成同步计划")
        rc = _gen_plan(plan_file)
        step_end(rc == 0)
        if rc != 0:
            log_error("plan 生成失败")
            harness_exit(3)
        if not os.path.isfile(plan_file) or os.path.getsize(plan_file) == 0:
            log_info("plan 为空，无操作（exit 4）")
            harness_exit(4)
        plan_lines = [l for l in Path(plan_file).read_text(encoding="utf-8").splitlines() if l and l[0] in "+-"]
        if not plan_lines:
            log_info("plan 为空，无操作（exit 4）")
            harness_exit(4)
        log_info(f"plan 归档: {plan_file}")
        log_info(f"PLAN 生成: plan_file={plan_file}, entries={len(plan_lines)}")

    elif mode == "apply":
        step_begin("阶段 2: 执行同步计划")
        if _apply_plan(args.plan_file):
            step_end(True)
        else:
            step_end(False)
            log_error("apply 失败，跳过校验")
            harness_exit(1)

        step_begin("阶段 3: 落盘校验")
        vrc = _verify_after_apply(args.plan_file)
        step_end(vrc)
        if not vrc:
            harness_exit(1)
        log_info(f"计划产物: {args.plan_file}")

    else:  # check-only
        plan_file = _tmp_file("preview-plan.tsv")
        step_begin("阶段 1: 生成同步计划")
        rc = _gen_plan(plan_file)
        step_end(rc == 0)
        if rc != 0:
            log_error("check-only: 扫描失败，无法生成差异预览")
            harness_exit(3)
        log_info(f"plan 归档: {plan_file}")
        print()
        log_info("差异预览")
        if os.path.isfile(plan_file):
            sys.stdout.write(Path(plan_file).read_text(encoding="utf-8"))

    harness_exit(0)


if __name__ == "__main__":
    main()

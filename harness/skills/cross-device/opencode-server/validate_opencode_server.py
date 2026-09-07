"""validate_opencode_server — 校验 opencode-server 脚本与 SKILL.md 一致性。

覆盖（迁移缺陷护栏，防回归）：
- 文件存在性（脚本 + SKILL.md）
- 无 `|| true` 吞错（须显式分支处理）
- EnvironmentFile 不硬编码 %h/$HOME，须引用 $SERVER_ENV_FILE（ENV_OPENCODE_SERVER_ENV_FILE 消费方）
- systemd unit 写入须为原子写（同目录临时文件 + mv -f）
- --help 须在 lc_init 之前拦截（消除日志/汇总副作用）
- 不引用 LcSkills core 运行时（core/lib、core/scripts、lc_bootstrap，已脱离 LcSkills）
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_MD = HERE / "SKILL.md"
SCRIPT = HERE / "start-opencode-server.sh"
LIB_SHELL_DIR = HERE / "lib" / "shell"

OR_TRUE_PATTERN = re.compile(r"\|\|\s*true\b")
ENV_FILE_HARDCODE = re.compile(r"EnvironmentFile=%h|EnvironmentFile=\$HOME\b")
ENV_FILE_VAR = re.compile(r"EnvironmentFile=\$SERVER_ENV_FILE\b")
ATOMIC_TMP = re.compile(r"\.\$\{SERVICE_UNIT\}\.tmp")
ATOMIC_MV = re.compile(r"mv\s+-f\s+\"\$tmp_file\"\s+\"\$SERVICE_FILE\"")
LCSKILLS_CORE_REF = re.compile(r"core/(scripts|lib)/|lc_bootstrap")
INIT_PATTERN = re.compile(r"\blc_init\s+\"")
HELP_PATTERN = re.compile(r"-h\s*\|\s*--help\)|--help\)")


def _bootstrap() -> None:
    _harness_lib = next(
        (p / "lib" for p in HERE.parents if (p / "lib" / "harness_lib.py").is_file()),
        None,
    )
    if _harness_lib is None:
        raise RuntimeError("找不到 harness/lib 锚点（harness_lib.py 缺失）")
    if str(_harness_lib) not in sys.path:
        sys.path.insert(0, str(_harness_lib))


_bootstrap()

from harness_lib import harness_exit, log_error, log_info  # noqa: E402


def validate_files_exist() -> list[str]:
    errors = []
    if not SCRIPT.is_file():
        errors.append(f"脚本不存在: {SCRIPT}")
    if not SKILL_MD.is_file():
        errors.append(f"SKILL.md 不存在: {SKILL_MD}")
    return errors


def _script_files() -> list[Path]:
    """护栏覆盖文件清单：主入口 + lib/shell/ 拆出件（CDP-09：规模拆分后
    拆出件同在护栏内，防 lib 注入 || true 等吞错逃逸检查）。"""
    files = [SCRIPT]
    if LIB_SHELL_DIR.is_dir():
        files.extend(sorted(LIB_SHELL_DIR.glob("*.sh")))
    return files


def _script_texts() -> list[tuple[str, str]]:
    """逐文件读取（保留来源标注），返回 [(文件名, 文本)]；缺文件跳过
    （存在性由 validate_files_exist 判红，此处不重复报）。"""
    out: list[tuple[str, str]] = []
    for f in _script_files():
        if f.is_file():
            out.append((f.name, f.read_text(encoding="utf-8")))
    return out


def _script_text() -> str:
    """全部护栏文件拼接文本（仅供无行号语义的整体正则校验使用）。"""
    return "\n".join(t for _, t in _script_texts())


def validate_no_true_swallow() -> list[str]:
    """禁止 `|| true` 吞错（须显式分支处理）。逐文件扫描并标注来源。"""
    errors = []
    for src, text in _script_texts():
        for lineno, line in enumerate(text.splitlines(), 1):
            if OR_TRUE_PATTERN.search(line):
                errors.append(f"{src} L{lineno} 存在 `|| true` 吞错: {line.strip()}")
    return errors


def validate_env_file() -> list[str]:
    """EnvironmentFile 不得硬编码 %h/$HOME，须引用 $SERVER_ENV_FILE（ENV_OPENCODE_SERVER_ENV_FILE）。"""
    if not SCRIPT.is_file():
        return []
    text = _script_text()
    errors = []
    if ENV_FILE_HARDCODE.search(text):
        errors.append("EnvironmentFile 硬编码 %h 或 $HOME（须改用 ENV_OPENCODE_SERVER_ENV_FILE）")
    if not ENV_FILE_VAR.search(text):
        errors.append("EnvironmentFile 未引用 $SERVER_ENV_FILE（ENV_OPENCODE_SERVER_ENV_FILE 消费方）")
    return errors


def validate_atomic_write() -> list[str]:
    """systemd unit 写入须为原子写（同目录临时文件 + mv -f，中断不留半写态）。"""
    if not SCRIPT.is_file():
        return []
    text = _script_text()
    errors = []
    if not ATOMIC_TMP.search(text):
        errors.append("未发现同目录临时文件写入模式（.${SERVICE_UNIT}.tmp）")
    if not ATOMIC_MV.search(text):
        errors.append('未发现原子替换（mv -f "$tmp_file" "$SERVICE_FILE"）')
    return errors


def validate_help_before_init() -> list[str]:
    """--help 须在 lc_init 之前拦截（消除日志/artifact 与运行汇总副作用）。

    仅扫主入口：lc_init/--help 均属主入口流程语义（lib 拆出件由入口在
    拦截之后 source，行号语义以主脚本为准）。"""
    if not SCRIPT.is_file():
        return []
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    init_lineno = next((i + 1 for i, line in enumerate(lines) if INIT_PATTERN.search(line)), None)
    help_lineno = next((i + 1 for i, line in enumerate(lines) if HELP_PATTERN.search(line)), None)
    errors = []
    if init_lineno is None:
        errors.append("未发现 lc_init 调用")
    if help_lineno is None:
        errors.append("未发现 --help 拦截分支")
    if init_lineno and help_lineno and help_lineno > init_lineno:
        errors.append(
            f"--help 拦截（L{help_lineno}）位于 lc_init（L{init_lineno}）之后，存在日志/汇总副作用"
        )
    return errors


def validate_no_lcskills_core_ref() -> list[str]:
    """迁移完整性：脚本不得再引用 LcSkills core 运行时（core/lib、core/scripts、lc_bootstrap）。"""
    errors = []
    for src, text in _script_texts():
        for lineno, line in enumerate(text.splitlines(), 1):
            if LCSKILLS_CORE_REF.search(line):
                errors.append(f"{src} L{lineno} 仍引用 LcSkills core 运行时: {line.strip()}")
    return errors


def validate_script_runs() -> list[str]:
    """脚本须可真跑：主入口 + lib/shell 拆出件逐个 bash -n 退 0；主入口
    --help 断言退 0 且含 usage（拆出件由入口 source 加载，语法错误同样
    在检查期暴露，不因拆分逃逸护栏）。
    """
    if not SCRIPT.is_file():
        return [f"脚本不存在: {SCRIPT}"]
    errors = []
    for f in _script_files():
        if not f.is_file():
            continue
        try:
            r = subprocess.run(["bash", "-n", str(f)],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            if r.returncode != 0:
                errors.append(f"{f.name} bash -n 语法检查失败: {r.stderr.strip()[:200]}")
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append(f"{f.name} bash -n 执行失败: {e}")
    try:
        r = subprocess.run(["bash", str(SCRIPT), "--help"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0:
            errors.append(f"--help 退出码 {r.returncode} != 0: "
                          f"{(r.stderr or r.stdout).strip()[:200]}")
        elif "usage" not in r.stdout.lower():
            errors.append("--help 输出不含 usage（用法说明缺失）")
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"--help 执行失败: {e}")
    return errors


def main() -> int:
    log_info("validate_opencode_server - 校验脚本与 SKILL.md 一致性")
    all_errors: list[str] = []
    all_errors.extend(validate_files_exist())
    all_errors.extend(validate_no_true_swallow())
    all_errors.extend(validate_env_file())
    all_errors.extend(validate_atomic_write())
    all_errors.extend(validate_help_before_init())
    all_errors.extend(validate_no_lcskills_core_ref())
    all_errors.extend(validate_script_runs())
    if all_errors:
        for e in all_errors:
            log_error(e)
        log_error(f"校验失败: {len(all_errors)} 个问题")
        return 1
    log_info("所有校验通过")
    return 0


if __name__ == "__main__":
    harness_exit(main())

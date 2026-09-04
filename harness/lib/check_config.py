#!/usr/bin/env python3
"""check_config.py — 配置与契约治理检查（接入 selfcheck 判红链）。

两种模式（互斥，默认 config）：
  check_config.py             # config 校验（方向 1/2）：
    - verify-cases.yaml modules 段：每模块齐备 targets/test_targets/push
      三键（非空列表），targets 跨模块唯一，push 目标（dst 嵌套列表展开）
      须绝对路径
    - cases 段：str 旧形态放行；dict 新形态仅许
      acceptance/setup_snapshot/teardown/timeout_s 四键且必含 acceptance，
      timeout_s 须正整数，setup_snapshot/teardown 须字符串列表
    - paths.conf：只含已知键（PATCHS_DIR/KERNEL_WS/AOSP_WS）且无缺漏；
      值中 ${VAR:-default} 语法按默认值展开
    - baseline-status.yaml 与 paths.conf 同名字段一致性：全文档递归搜
      与 paths 键同名（或全小写）的字段，出现即须与 paths.conf 值一致
      （当前登记表无同名字段 → 检查自然通过；未来写入即受约束）
  check_config.py --contract  # command 与 skill 契约检查（方向 3）：
    - .opencode/command/*.md 实际集合遍历：非豁免 command 须有同名
      harness/skills/<name>/SKILL.md；豁免清单见 _EXEMPT_COMMANDS
    - harness/skills/* 实际集合遍历：每个 skill 目录须有 SKILL.md
    - 按实际文件集合核对，不固化数量（新增/删除文件自动纳入检查面）

退出码：0 全过 / 1 有违规（明细列 stdout，末行结论供 selfcheck 摘要拼接）。
ROOT 可经 CHECK_CONFIG_ROOT 环境变量注入（单测隔离）。
"""
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("CHECK_CONFIG_ROOT",
                           Path(__file__).resolve().parents[2]))

# paths.conf 已知键（AGENTS.md：路径单一事实源，paths.py 按此读取；
# LC_VERIFY_EXPECT_SERIAL 为设备身份期望序列号配置位，方向 1 接入）
_PATHS_KEYS = ("PATCHS_DIR", "KERNEL_WS", "AOSP_WS", "LC_VERIFY_EXPECT_SERIAL")
# paths.conf 值默认值展开：${VAR:-default} → default（环境覆盖不影响
# 文件一致性的判定基准，统一按文件面比较）
_VAR_DEFAULT_RE = re.compile(r"\$\{\w+:-(.*?)\}")

# cases 段 dict 形态允许的键（生命周期资产，方向 1）
_CASE_DICT_KEYS = {"acceptance", "setup_snapshot", "teardown", "timeout_s"}

# 契约豁免清单（方向 3）：
#   opencode-server —— opencode 内建服务入口，不对应 harness skill；
#   cross-device-apply / cross-device-emit —— SKILL 发布态在 opencode 侧
#     （lc-skills-cross-device-*），harness 侧对应 cross-device/ 共享库
#     目录（无独立 SKILL.md 属合法形态），不按"同名 skill 直配"核对；
# 新增非 skill 类 command 须在此登记并注明形态
_EXEMPT_COMMANDS = {"opencode-server", "cross-device-apply", "cross-device-emit"}
# skill 目录豁免（共享库目录，非 skill 契约面）
_SKILL_LIB_DIRS = {"cross-device"}
# 非目录噪声（缓存/隐藏目录不纳入契约面）
_NOISE_DIRS = {"__pycache__", ".pytest_cache"}


def _fail(errors, msg):
    errors.append(msg)


def check_verify_cases(root):
    """verify-cases.yaml 校验（方向 1），返回违规列表。"""
    errors = []
    path = root / "harness" / "config" / "verify-cases.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return [f"verify-cases.yaml 读取失败: {e}"]
    modules = data.get("modules")
    if not isinstance(modules, dict) or not modules:
        _fail(errors, "modules 段缺失或为空")
        modules = {}
    seen_targets = {}
    for name, mod in modules.items():
        if not isinstance(mod, dict):
            _fail(errors, f"modules.{name} 须为映射")
            continue
        for key in ("targets", "test_targets", "push"):
            v = mod.get(key)
            if not isinstance(v, list) or not v:
                _fail(errors, f"modules.{name} 缺 {key}（须非空列表）")
        for t in mod.get("targets") or []:
            if t in seen_targets:
                _fail(errors, f"targets 重复: {t!r}（modules.{seen_targets[t]}"
                              f" 与 modules.{name}）")
            else:
                seen_targets[t] = name
        for entry in mod.get("push") or []:
            if not isinstance(entry, dict):
                _fail(errors, f"modules.{name}.push 含非法项（须映射）")
                continue
            for dst in entry.get("dst") or []:
                if not isinstance(dst, str) or not dst.startswith("/"):
                    _fail(errors, f"push 目标须绝对路径: {dst!r}"
                                  f"（modules.{name}.{entry.get('module')}）")
    cases = data.get("cases")
    if not isinstance(cases, dict) or not cases:
        _fail(errors, "cases 段缺失或为空")
        cases = {}
    for name, val in cases.items():
        if not isinstance(val, dict):
            continue  # str 旧形态放行（无生命周期）
        unknown = set(val) - _CASE_DICT_KEYS
        if unknown:
            _fail(errors, f"cases.{name} 含未知键: {', '.join(sorted(unknown))}"
                          f"（仅许 {', '.join(sorted(_CASE_DICT_KEYS))}）")
        if not (val.get("acceptance") or "").strip():
            _fail(errors, f"cases.{name} 缺 acceptance（dict 形态必填）")
        for key in ("setup_snapshot", "teardown"):
            v = val.get(key)
            if v is None:
                continue
            if not isinstance(v, list) or \
                    any(not isinstance(x, str) for x in v):
                _fail(errors, f"cases.{name}.{key} 须为字符串列表")
        ts = val.get("timeout_s")
        if ts is not None and (not isinstance(ts, int)
                               or isinstance(ts, bool) or ts <= 0):
            _fail(errors, f"cases.{name}.timeout_s 须为正整数（实际 {ts!r}）")
    return errors


def _parse_paths_conf(root):
    """解析 paths.conf，返回 (键值 dict, 违规列表)；值按 ${VAR:-default} 展开。"""
    errors = []
    path = root / "harness" / "config" / "paths.conf"
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return {}, [f"paths.conf 读取失败: {e}"]
    known = set(_PATHS_KEYS)
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m:
            _fail(errors, f"paths.conf 非法行（须 KEY=value）: {raw.strip()[:60]}")
            continue
        key, val = m.group(1), m.group(2).strip().strip('"')
        if key not in known:
            _fail(errors, f"paths.conf 未知键: {key}（已知: "
                          f"{', '.join(_PATHS_KEYS)}）")
            continue
        values[key] = _VAR_DEFAULT_RE.sub(r"\1", val)
    missing = known - set(values)
    if missing:
        _fail(errors, f"paths.conf 缺已知键: {', '.join(sorted(missing))}")
    return values, errors


def _iter_dict_keys(node):
    """递归产出 (key, value)（仅 dict 值的键层，含嵌套）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _iter_dict_keys(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dict_keys(item)


def check_paths_vs_baseline(root, path_values):
    """paths.conf 与 baseline-status.yaml 同名字段一致性（方向 2）。"""
    errors = []
    path = root / "harness" / "config" / "baseline-status.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        return [f"baseline-status.yaml 读取失败: {e}"]
    for key, expected in path_values.items():
        for name, val in _iter_dict_keys(data):
            if name in (key, key.lower()):
                if str(val).strip() != str(expected).strip():
                    _fail(errors, f"baseline-status.yaml 字段 {name}({val!r}) "
                                  f"与 paths.conf {key}({expected!r}) 不一致")
                break  # 同名取首个出现（一处不一致即报，不重复计）
    return errors


def check_contract(root):
    """command 与 skill 契约检查（方向 3），返回违规列表。

    按实际文件集合遍历核对（不固化数量）：非豁免 command ↔ 同名 skill
    （SKILL.md 存在）；skill 目录齐 SKILL.md。
    """
    errors = []
    cmd_dir = root / ".opencode" / "command"
    skills_dir = root / "harness" / "skills"
    commands = sorted(p.stem for p in cmd_dir.glob("*.md")) if cmd_dir.is_dir() else []
    if not commands:
        _fail(errors, f"未发现 command 文件（{cmd_dir}）")
    skills = sorted(
        p.name for p in skills_dir.iterdir()
        if p.is_dir() and p.name not in _NOISE_DIRS
        and not p.name.startswith(".")) if skills_dir.is_dir() else []
    for name in commands:
        if name in _EXEMPT_COMMANDS:
            continue
        skill_md = skills_dir / name / "SKILL.md"
        if not skill_md.is_file():
            _fail(errors, f"command {name} 无对应 skill"
                          f"（缺 {skill_md.relative_to(root)}）；"
                          "非 skill 类 command 须登记豁免清单")
    for name in skills:
        if name in _SKILL_LIB_DIRS:
            continue
        if not (skills_dir / name / "SKILL.md").is_file():
            _fail(errors, f"skill {name} 缺 SKILL.md")
    return errors


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    contract = "--contract" in argv
    if contract:
        argv.remove("--contract")
    if argv:
        print(f"error: 未知参数 {argv}", file=sys.stderr)
        return 2
    if contract:
        errors = check_contract(ROOT)
        label = "contract"
    else:
        errors = []
        path_values, perr = _parse_paths_conf(ROOT)
        errors += perr
        errors += check_verify_cases(ROOT)
        errors += check_paths_vs_baseline(ROOT, path_values)
        label = "config"
    for e in errors:
        print(f"[VIOLATION] {e}")
    if errors:
        print(f"==== {label}: 共 {len(errors)} 处违规（判红）====")
        return 1
    print(f"OK: {label} 检查通过，无违规。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

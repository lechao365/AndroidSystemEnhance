#!/usr/bin/env python3
"""check_config.py — 配置与契约治理检查（接入 selfcheck 判红链）。

三种模式（--all 与单模式互斥，默认 config）：
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
  check_config.py --all       # config+contract 单进程双模式（B2，selfcheck
    提速：消两次 Python 启动与两遍 yaml 导入/全量扫描）。输出两段结论 +
    末尾两行机器可读 rc（config_rc=<n> / contract_rc=<n>，供 selfcheck
    分别判红）；单模式输出格式不变。

退出码：0 全过 / 1 有违规（--all 时任一段违规即 1；明细列 stdout，
结论行供 selfcheck 摘要拼接）。
ROOT 可经 CHECK_CONFIG_ROOT 环境变量注入（单测隔离）。
"""
import os
import re
import sys
from pathlib import Path

import yaml

# CHECK_CONFIG_ROOT 为空串时视为未设置：Path("") 会解析成当前目录（.）致
# 检查根漂移，取值须 strip 后判空再回落默认值。
_CFG_ROOT = os.environ.get("CHECK_CONFIG_ROOT", "").strip()
ROOT = Path(_CFG_ROOT) if _CFG_ROOT else Path(__file__).resolve().parents[2]

# paths.conf 已知键（AGENTS.md：路径单一事实源，paths.py 按此读取；
# LC_VERIFY_EXPECT_SERIAL 为设备身份期望序列号配置位，方向 1 接入）
_PATHS_KEYS = ("PATCHS_DIR", "KERNEL_WS", "AOSP_WS", "LC_VERIFY_EXPECT_SERIAL")
# paths.conf 值默认值展开：${VAR:-default} → default（环境覆盖不影响
# 文件一致性的判定基准，统一按文件面比较）
_VAR_DEFAULT_RE = re.compile(r"\$\{\w+:-(.*?)\}")

# cases 段 dict 形态允许的键（生命周期资产，方向 1）
_CASE_DICT_KEYS = {"acceptance", "setup_snapshot", "teardown", "timeout_s"}

# modules 段已知键白名单（批次六 D7）：拼错键（如 test_taget）会静默被
# 消费方忽略、用例行为与登记脱节——未知键直接判红
_MODULE_KEYS = {"targets", "test_targets", "push", "test_src",
                "test_targets_run_as_root"}

# doc-sync-mapping.yaml route 合法 mode 与键（批次六 D8：映射规则治理，
# 拼错 mode/缺 priority 会让 /sync-code-to-doc 分发静默漂移）
_DOC_SYNC_MODES = {"fixed", "ai-diff", "ai-pending"}
_DOC_SYNC_KEYS = {"match", "docs", "mode", "priority", "note"}

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
        # 批次六 D7：未知键判红（白名单防拼写静默脱管）
        unknown_keys = set(mod) - _MODULE_KEYS
        if unknown_keys:
            _fail(errors, f"modules.{name} 含未知键: "
                          f"{', '.join(sorted(unknown_keys))}"
                          f"（已知: {', '.join(sorted(_MODULE_KEYS))}）")
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
            # 批次六 D7：dst 必填（原 `or []` 空迭代静默放行，缺 dst 的
            # push 项会被消费方跳过而不被发现）
            dst_list = entry.get("dst")
            if not isinstance(dst_list, list) or not dst_list:
                _fail(errors, f"modules.{name}.push 缺 dst（须非空路径列表）")
                continue
            for dst in dst_list:
                if not isinstance(dst, str) or not dst.startswith("/"):
                    _fail(errors, f"push 目标须绝对路径: {dst!r}"
                                  f"（modules.{name}.{entry.get('module')}）")
    cases = data.get("cases")
    if not isinstance(cases, dict) or not cases:
        _fail(errors, "cases 段缺失或为空")
        cases = {}
    for name, val in cases.items():
        if val is None:
            # 批次六 D7：`cases.xxx:`（YAML 空值）原被当非 dict 放行，
            # 用例无 acceptance 等生命周期定义却占位——判红
            _fail(errors, f"cases.{name} 值为空（None）——须 dict 形态"
                          "（含 acceptance）或 str 旧形态")
            continue
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


class _DupKeyLoader(yaml.SafeLoader):
    """拦截 YAML 重复映射键：safe_load 默认静默取后值，doc-sync-mapping
    实测出现过 mode 键重复——重复即判红（构造函数入违观数组）。"""
    _dup_hits = []

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for k_node, _ in node.value:
            key = self.construct_object(k_node, deep=deep)
            if key in seen:
                self._dup_hits.append(str(key))
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def check_doc_sync_mapping(root):
    """doc-sync-mapping.yaml 映射规则治理（批次六 D8），返回违规列表。

    检查面：YAML 重复键（含嵌套，_DupKeyLoader 拦截）；routes 段存在且
    为列表；每 route 键白名单（match/docs/mode/priority/note）；match
    非空字符串；docs 须字符串列表；mode ∈ {fixed, ai-diff, ai-pending}；
    fixed/ai-diff 须 priority 数字（分发按 priority 降序，缺了排序漂移）。
    """
    errors = []
    path = root / "harness" / "config" / "doc-sync-mapping.yaml"
    try:
        _DupKeyLoader._dup_hits = []
        data = yaml.load(path.read_text(encoding="utf-8"),
                         Loader=_DupKeyLoader) or {}
    except (OSError, yaml.YAMLError) as e:
        return [f"doc-sync-mapping.yaml 读取失败: {e}"]
    for k in _DupKeyLoader._dup_hits:
        _fail(errors, f"doc-sync-mapping.yaml 重复键: {k!r}（后值静默覆盖，须删一）")
    routes = data.get("routes")
    if not isinstance(routes, list) or not routes:
        _fail(errors, "doc-sync-mapping.yaml routes 段缺失或为空")
        return errors
    for i, route in enumerate(routes):
        label = f"routes[{i}]"
        if not isinstance(route, dict):
            _fail(errors, f"doc-sync-mapping.yaml {label} 须为映射")
            continue
        unknown = set(route) - _DOC_SYNC_KEYS
        if unknown:
            _fail(errors, f"doc-sync-mapping.yaml {label} 含未知键: "
                          f"{', '.join(sorted(unknown))}（仅许 "
                          f"{', '.join(sorted(_DOC_SYNC_KEYS))}）")
        match = route.get("match")
        if not isinstance(match, str) or not match.strip():
            _fail(errors, f"doc-sync-mapping.yaml {label} 缺 match 或非字符串")
        docs = route.get("docs")
        if not isinstance(docs, list) or \
                any(not isinstance(d, str) for d in docs):
            _fail(errors, f"doc-sync-mapping.yaml {label}.docs 须为字符串列表")
        mode = route.get("mode")
        if mode not in _DOC_SYNC_MODES:
            _fail(errors, f"doc-sync-mapping.yaml {label}.mode 非法: "
                          f"{mode!r}（须 {'/'.join(sorted(_DOC_SYNC_MODES))}）")
        if mode in ("fixed", "ai-diff"):
            prio = route.get("priority")
            if not isinstance(prio, (int, float)) or isinstance(prio, bool):
                _fail(errors, f"doc-sync-mapping.yaml {label}（{mode}）"
                              "缺 priority 或非数字")
    return errors


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    contract = "--contract" in argv
    if contract:
        argv.remove("--contract")
    all_mode = "--all" in argv
    if all_mode:
        argv.remove("--all")
    if argv or (contract and all_mode):
        print(f"error: 未知参数 {argv}（--all 与 --contract 互斥）",
              file=sys.stderr)
        return 2

    def _emit(label, errors):
        """单段检查输出：违规明细 + 结论行；返回 rc。"""
        for e in errors:
            print(f"[VIOLATION] {e}")
        if errors:
            print(f"==== {label}: 共 {len(errors)} 处违规（判红）====")
            return 1
        print(f"OK: {label} 检查通过，无违规。")
        return 0

    if all_mode:
        path_values, perr = _parse_paths_conf(ROOT)
        cfg_rc = _emit("config", perr + check_verify_cases(ROOT)
                       + check_paths_vs_baseline(ROOT, path_values)
                       + check_doc_sync_mapping(ROOT))
        ctr_rc = _emit("contract", check_contract(ROOT))
        # 机器可读 rc 行（selfcheck --all 解析分判红；人类结论行在上）
        print(f"config_rc={cfg_rc}")
        print(f"contract_rc={ctr_rc}")
        return 1 if cfg_rc or ctr_rc else 0

    if contract:
        return _emit("contract", check_contract(ROOT))
    path_values, perr = _parse_paths_conf(ROOT)
    return _emit("config", perr + check_verify_cases(ROOT)
                 + check_paths_vs_baseline(ROOT, path_values)
                 + check_doc_sync_mapping(ROOT))


if __name__ == "__main__":
    sys.exit(main())

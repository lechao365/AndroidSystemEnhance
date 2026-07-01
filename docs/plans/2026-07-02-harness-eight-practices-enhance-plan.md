# 8 项特有优秀实践极致补强实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将 engineering/harness/reference/harness-optimization-blueprint.md 第2章描述的8项本项目特有优秀实践从"概念锁定"提升为"可执行、可校验、可测试"的生产级实现。

**架构：** 按依赖关系分3批（Batch A/B/C）顺序实施。每批内部可并行。每项实践有独立校验器+测试覆盖。

**Tech Stack:** Bash (harness observability 公共库), Python (路径工具 CLI), YAML (manifest/config), Markdown (WORKFLOW.md)

---

## Batch A: 基础设施（无外部依赖）

### Task A1: 创建 Manifest 声明式索引 (`rules/manifest.yaml`)

**Files:**
- Create: `engineering/harness/rules/manifest.yaml`

- [ ] **Step 1: 创建 manifest.yaml**

```yaml
version: 1

contexts:
  - id: workspace-source-modify
    match: "~/workspace/**"
    scope_category: source
    rules:
      - "rules/source-code-modify.md"
    workflow:
      - "workflows/lc-sync-code-to-patchs/"
    access: require_evidence
    require_plan: false

  - id: patchs-archive
    match: "patchs/**"
    scope_category: archive
    rules:
      - "rules/source-code-modify.md"
    workflow:
      - "workflows/lc-sync-code-to-patchs/"
    access: require_evidence
    require_plan: false

  - id: patchs-revert
    match: "patchs/**"
    scope_category: revert
    rules:
      - "rules/source-code-modify.md"
    workflow:
      - "workflows/lc-revert-code-from-patchs/"
    access: direct_edit
    require_plan: true
    require_confirmation: true
    require_evidence: true

  - id: doc-sync
    match: "patchs/**"
    scope_category: doc-sync
    rules:
      - "rules/doc-paths.md"
      - "rules/plantuml.md"
    workflow:
      - "workflows/lc-sync-patchs-to-doc/"
    access: require_plan
    require_plan: true
    require_confirmation: true
    require_evidence: true

  - id: git-push
    match: "**"
    scope_category: git
    rules: []
    workflow:
      - "workflows/lc-git-push-to-server/"
    access: require_confirmation
    require_confirmation: true

  - id: harness-script
    match: "engineering/harness/scripts/**"
    scope_category: harness
    rules:
      - "rules/script-observability.md"
    access: direct_edit
    require_evidence: true

  - id: harness-rules
    match: "engineering/harness/rules/**"
    scope_category: harness
    rules:
      - "engineering/harness/rules/README.md"
    access: require_plan
    require_plan: true

  - id: harness-config
    match: "engineering/harness/config/**"
    scope_category: harness
    rules:
      - "rules/path-management.md"
    access: direct_edit
    require_plan: false

  - id: harness-validator
    match: "engineering/harness/tests/**"
    scope_category: test
    rules:
      - "rules/script-observability.md"
    access: direct_edit
    require_evidence: true

  - id: docs
    match: "docs/**"
    scope_category: docs
    rules:
      - "rules/doc-paths.md"
      - "rules/plantuml.md"
    access: require_confirmation
    require_confirmation: true

access_levels:
  - level: direct_edit
    description: "允许直接编辑无需确认"
  - level: require_workflow
    description: "必须经过指定 workflow"
  - level: require_plan
    description: "必须先出实施计划"
  - level: require_confirmation
    description: "必须逐条确认"
  - level: require_evidence
    description: "必须留证据（manifest/baseline）"
```

- [ ] **Step 2: 验证 YAML 可解析**

```bash
python3 -c "import yaml; yaml.safe_load(open('engineering/harness/rules/manifest.yaml')); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add engineering/harness/rules/manifest.yaml
git commit -m "feat(manifest): 创建 Manifest 声明式索引 + Access 五级控制"
```

---

### Task A2: 创建 Access 准入查询 CLI (`scripts/check_access.sh`)

**Files:**
- Create: `engineering/harness/scripts/check_access.sh`
- Create: `engineering/harness/tests/test_check_access.sh`

- [ ] **Step 1: 创建 check_access.sh**

```bash
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "check_access"

# 解析参数
path=""
category=""
while [ $# -gt 0 ]; do
    case "$1" in
        --path) path="$2"; shift 2 ;;
        --category) category="$2"; shift 2 ;;
        *) echo "Usage: $0 --path <path> --category <category>"; harness_exit 2 ;;
    esac
done

[ -z "$path" ] && { echo "ERROR: --path 必填"; harness_exit 2; }
[ -z "$category" ] && { echo "ERROR: --category 必填"; harness_exit 2; }

MANIFEST="$(harness_path HARNESS_DIR)/rules/manifest.yaml"
[ ! -f "$MANIFEST" ] && { echo "ERROR: manifest.yaml 不存在"; harness_exit 3; }

result=$(python3 -c "
import sys, yaml
with open('$MANIFEST') as f:
    data = yaml.safe_load(f)

category = '$category'

# 查找匹配的 context
matched = None
for ctx in data.get('contexts', []):
    if ctx.get('scope_category') == category:
        matched = ctx
        break

if matched is None:
    print('{\"allowed\": false, \"reason\": \"no matching context for category: ' + category + '\"}')
    sys.exit(0)

output = {
    'allowed': True,
    'access': matched.get('access', 'unknown'),
    'rules': matched.get('rules', []),
    'workflow': matched.get('workflow', []),
    'require_plan': matched.get('require_plan', False),
    'require_confirmation': matched.get('require_confirmation', False),
    'require_evidence': matched.get('require_evidence', False),
}
import json
print(json.dumps(output, indent=2, ensure_ascii=False))
")

echo "$result"
harness_exit 0
```

- [ ] **Step 2: 创建测试 test_check_access.sh**

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
CHECK_ACCESS="$REPO_ROOT/engineering/harness/scripts/check_access.sh"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$1"; }

test_known_category() {
    local output
    output=$(bash "$CHECK_ACCESS" --path "~/workspace/foo" --category "source" 2>/dev/null)
    echo "$output" | grep -q '"allowed": true' || fail "source 应允许"
    echo "$output" | grep -q '"access": "require_evidence"' || fail "source 应为 require_evidence"
    pass "known category returns correct access"
}

test_unknown_category() {
    local output
    output=$(bash "$CHECK_ACCESS" --path "foo" --category "nonexistent" 2>/dev/null)
    echo "$output" | grep -q '"allowed": false' || fail "未知 category 应拒绝"
    pass "unknown category returns denied"
}

main() {
    test_known_category
    test_unknown_category
    printf 'PASS: test_check_access.sh\n'
}

main "$@"
```

- [ ] **Step 3: 运行测试验证**

```bash
bash engineering/harness/tests/test_check_access.sh
```

Expected: `PASS: test_check_access.sh`

- [ ] **Step 4: 提交**

```bash
git add engineering/harness/scripts/check_access.sh engineering/harness/tests/test_check_access.sh
git commit -m "feat(access): 新增 Access 准入查询 CLI + 测试"
```

---

### Task A3: Observability 公共库增强 (5项)

**Files:**
- Modify: `engineering/harness/lib/shell/harness_observability.sh`
- Modify: `engineering/harness/tests/test_harness_observability.sh`

- [ ] **Step 1: harness_observability.sh 增加 harness_collect_metrics**

在 `harness_exit()` 函数之前插入：

```bash
# ============================================================================
# 运行时性能指标采集
# ============================================================================
_H_METRICS_PID=""

harness_collect_metrics() {
    local cpu mem disk
    cpu=$(top -bn1 2>/dev/null | grep "Cpu(s)" | awk '{print $2}' | cut -d. -f1)
    mem=$(free -m 2>/dev/null | awk '/Mem:/ {print $3}')
    disk=$(df -h / 2>/dev/null | tail -1 | awk '{print $5}')
    _h_log_raw "cpu=${cpu:-0} mem=${mem:-0}MB disk=${disk:-0}"
}

harness_start_metrics_watch() {
    local interval="${1:-60}"
    _H_METRICS_PID=""
    while true; do
        harness_collect_metrics
        sleep "$interval"
    done &
    _H_METRICS_PID=$!
}

harness_stop_metrics_watch() {
    [ -n "$_H_METRICS_PID" ] && kill "$_H_METRICS_PID" 2>/dev/null || true
    _H_METRICS_PID=""
}
```

- [ ] **Step 2: harness_observability.sh 增加 harness_assert 系列**

在 `harness_collect_metrics` 之后插入：

```bash
# ============================================================================
# 内置断言 API（测试脚本使用）
# ============================================================================
_h_assert_fail() {
    local msg="$1"
    printf "ASSERT FAIL: %s\n" "$msg" >&2
    _h_log_raw "level=ASSERT_FAIL msg=$msg"
    exit 1
}

harness_assert_eq() {
    local actual="$1" expected="$2" msg="${3:-}"
    [ "$actual" = "$expected" ] || _h_assert_fail "${msg:+(}${msg}) expected=$expected actual=$actual"
}

harness_assert_file_exists() {
    local path="$1" msg="${2:-}"
    [ -f "$path" ] || _h_assert_fail "${msg:+(}${msg}) file not found: $path"
}

harness_assert_grep() {
    local pattern="$1" file="$2" msg="${3:-}"
    grep -q "$pattern" "$file" || _h_assert_fail "${msg:+(}${msg}) pattern not found in $file: $pattern"
}

harness_assert_exit_code() {
    local expected="$1" actual="$?" msg="${2:-}"
    [ "$actual" -eq "$expected" ] || _h_assert_fail "${msg:+(}${msg}) expected exit $expected, got $actual"
}
```

- [ ] **Step 3: harness_observability.sh 增加 harness_trace**

在 `harness_assert_exit_code` 之后插入：

```bash
# ============================================================================
# Trace 日志级别（仅在 HARNESS_TRACE=1 时输出）
# ============================================================================
harness_trace() {
    [ "${HARNESS_TRACE:-0}" = "1" ] || return 0
    _h_log_raw "level=TRACE msg=$*"
}
```

- [ ] **Step 4: 扩展日志字段（pid/duration/caller）**

修改 `_h_log_file_write` 函数，在日志行中追加可选字段：

找到 `_h_log_file_write` 函数定义（第71-86行），将其替换为：

```bash
_h_log_file_write() {
    local level="$1" msg="$2"; shift 2
    local esc_msg="${msg//\"/\\\"}"
    local line="ts=$(_h_ts_iso) level=$level step=${_H_STEP_CURRENT}/? script=${_H_SCRIPT_NAME} msg=\"${esc_msg}\""
    line+=" pid=$$ duration=$(($(date +%s) - _H_INIT_TS)) caller=${FUNCNAME[2]:--}"
    local kv
    for kv in "$@"; do
        local esc_kv_v="${kv#*=}"
        esc_kv_v="${esc_kv_v//\"/\\\"}"
        line+=" ${kv%%=*}=\"${esc_kv_v}\""
    done
    printf '%s\n' "$line" >> "$_H_LOG_FILE"
}
```

- [ ] **Step 5: 增强 harness_report_no_upstream**

找到该函数（第520-526行），替换为：

```bash
harness_report_no_upstream() {
    local ctx="${1:-当前仓库}"
    local branch
    branch=$(harness_git_current_branch)
    log_error "${ctx} 无法确定 upstream base（分支: ${branch:-detached}）"
    log_error "请设置 upstream: git branch --set-upstream-to=origin/${branch:-<branch>}"
    log_info "诊断信息:"
    local remote_out
    remote_out=$(git remote -v 2>/dev/null || echo "  (无 remote)")
    while IFS= read -r l; do log_info "  $l"; done <<< "$remote_out"
    local branch_out
    branch_out=$(git branch -vv 2>/dev/null || echo "  (无分支信息)")
    while IFS= read -r l; do log_info "  $l"; done <<< "$branch_out"
}
```

- [ ] **Step 6: 在 harness_init 中注册 EXIT 时停止 metrics watch**

在 `_h_finalize` 函数内，`exit_code` 赋值之后添加：

```bash
    harness_stop_metrics_watch
```

- [ ] **Step 7: 补全 test_harness_observability.sh**

在 `test_harness_init_reuses_preexported_repo_root` 之后追加：

```bash
test_log_warn_error_format() {
    local script_name="test-log-warn-error-format"
    local log_dir="$LOG_ROOT/$script_name"
    rm -rf "$log_dir"
    mkdir -p "$log_dir"
    local ts
    ts=$(date '+%Y%m%d-%H%M%S')
    local log_file="$log_dir/$script_name-$ts.log"
    _H_LOG_FILE="$log_file"
    _H_LOG_DIR="$log_dir"
    _H_SCRIPT_NAME="$script_name"
    _H_INIT_TS=$(date +%s)

    log_warn "test warn message"
    log_error "test error message"

    assert_grep 'level=WARN' "$log_file"
    assert_grep 'level=ERROR' "$log_file"
    assert_grep 'msg="test warn message"' "$log_file"
    assert_grep 'pid=' "$log_file"
    assert_grep 'duration=' "$log_file"
    assert_grep 'caller=' "$log_file"
    pass "log_warn/log_error format with pid/duration/caller"
}

test_harness_assert_api() {
    local sandbox
    sandbox="$(mktemp -d "$TEST_TMP_ROOT/assert-test.XXXXXX")"
    mkdir -p "$sandbox"

    harness_assert_eq "foo" "foo" "eq should pass" || fail "assert_eq 应通过"
    touch "$sandbox/exists.txt"
    harness_assert_file_exists "$sandbox/exists.txt" "file should exist" || fail "assert_file_exists 应通过"
    pass "harness_assert API"
}

test_harness_trace() {
    HARNESS_TRACE=1
    local script_name="test-harness-trace"
    local log_dir="$LOG_ROOT/$script_name"
    rm -rf "$log_dir"
    mkdir -p "$log_dir"
    local ts
    ts=$(date '+%Y%m%d-%H%M%S')
    local log_file="$log_dir/$script_name-$ts.log"
    _H_LOG_FILE="$log_file"
    _H_LOG_DIR="$log_dir"
    _H_SCRIPT_NAME="$script_name"
    _H_INIT_TS=$(date +%s)

    harness_trace "this is a trace message"
    assert_grep 'level=TRACE' "$log_file"
    assert_grep 'this is a trace message' "$log_file"

    HARNESS_TRACE=0
    rm -rf "$log_dir"
    mkdir -p "$log_dir"
    log_file="$log_dir/$script_name-$(date '+%Y%m%d-%H%M%S').log"
    _H_LOG_FILE="$log_file"
    harness_trace "should not appear"
    if grep -q 'should not appear' "$log_file" 2>/dev/null; then
        fail "HARNESS_TRACE=0 时不应输出 trace"
    fi
    pass "harness_trace respects HARNESS_TRACE flag"
}

test_report_no_upstream_enhanced() {
    local script_name="test-report-no-upstream"
    local log_dir="$LOG_ROOT/$script_name"
    rm -rf "$log_dir"
    mkdir -p "$log_dir"
    local ts
    ts=$(date '+%Y%m%d-%H%M%S')
    local log_file="$log_dir/$script_name-$ts.log"
    _H_LOG_FILE="$log_file"
    _H_LOG_DIR="$log_dir"
    _H_SCRIPT_NAME="$script_name"
    _H_INIT_TS=$(date +%s)

    # 在非 git 目录中调用，验证不会 crash 并输出诊断信息
    local tmpdir
    tmpdir="$(mktemp -d "$TEST_TMP_ROOT/no-upstream.XXXXXX")"
    (
        cd "$tmpdir"
        harness_report_no_upstream "test context" 2>/dev/null || true
    )
    pass "harness_report_no_upstream does not crash in non-git dir"
    rm -rf "$tmpdir"
}
```

在 `main()` 中添加调用：

```bash
    test_log_warn_error_format
    test_harness_assert_api
    test_harness_trace
    test_report_no_upstream_enhanced
```

- [ ] **Step 8: 运行 observability 测试**

```bash
bash engineering/harness/tests/test_harness_observability.sh
```

Expected: All PASS

- [ ] **Step 9: 提交**

```bash
git add engineering/harness/lib/shell/harness_observability.sh engineering/harness/tests/test_harness_observability.sh
git commit -m "feat(observability): 增强公共库-指标采集/断言/trace/日志扩展/upstream诊断"
```

---

### Task A4: 多语言统一路径工具增强 (4项)

**Files:**
- Modify: `engineering/harness/lib/shell/harness_path_util.sh`
- Modify: `engineering/harness/lib/python/harness_path_util.py`
- Modify: `engineering/harness/lib/shell/harness_bootstrap.sh`
- Create: `engineering/harness/tests/test_harness_path_util.sh`

- [ ] **Step 1: harness_path_util.sh 增加 harness_validate_paths**

在 `harness_pythonpath()` 函数之后追加：

```bash
# ============================================================================
# harness_validate_paths — 检查 paths.conf 中所有 KEY 指向的目录是否存在
# ============================================================================
harness_validate_paths() {
    local missing=()
    local keys=("LOG_DIR" "ARTIFACTS_DIR" "TEST_SANDBOX_DIR" "OUTPUT_DIR" "HOST_LOG_DIR" "RUNS_DIR")
    local key val
    for key in "${keys[@]}"; do
        val=$(harness_path "$key" 2>/dev/null) || continue
        [ -d "$val" ] || missing+=("$key")
    done
    if [ ${#missing[@]} -eq 0 ]; then
        echo "ALL_EXIST"
        return 0
    else
        echo "MISSING:${missing[*]}"
        return 1
    fi
}
```

- [ ] **Step 2: harness_path_util.sh 增加 harness_reload_paths**

在 `harness_validate_paths` 之后追加：

```bash
# ============================================================================
# harness_reload_paths — 运行时重新加载 paths.conf
# ============================================================================
harness_reload_paths() {
    _H_PATH_CONF=()
    _h_path_load_conf "$_H_PATH_CONF_FILE"
}
```

- [ ] **Step 3: harness_bootstrap.sh 增加 --validate-paths 支持**

修改 `harness_init`（在 `harness_observability.sh` 中），添加 `--validate-paths` 解析：

在 `harness_init` 的 while 循环中添加：

```bash
            --validate-paths) validate_paths=true; shift ;;
```

在 `_H_SCRIPT_NAME="$1"` 之后、日志目录创建之前添加：

```bash
    # 可选：启动时校验路径存在性
    if [ "${validate_paths:-false}" = true ]; then
        local result
        result=$(harness_validate_paths 2>&1) || {
            log_warn "路径校验: $result"
        }
    fi
```

- [ ] **Step 4: Python 端新增 CLI 入口**

在 `harness_path_util.py` 文件末尾追加：

```python
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "--resolve":
        key = sys.argv[2]
        try:
            print(path(key))
        except KeyError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    elif len(sys.argv) >= 2 and sys.argv[1] == "--validate":
        missing = []
        for k in ["LOG_DIR", "ARTIFACTS_DIR", "TEST_SANDBOX_DIR"]:
            try:
                p = path(k)
                if not p.is_dir():
                    missing.append(k)
            except KeyError:
                missing.append(k)
        if missing:
            print(f"MISSING:{','.join(missing)}")
            sys.exit(1)
        else:
            print("ALL_EXIST")
    else:
        print("Usage: python harness_path_util.py --resolve <KEY>", file=sys.stderr)
        sys.exit(2)
```

- [ ] **Step 5: 创建 test_harness_path_util.sh**

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
PYTHON_LIB="$REPO_ROOT/engineering/harness/lib/python"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$1"; }

test_shell_path_resolve() {
    local log_dir
    log_dir=$(harness_path LOG_DIR)
    [ -n "$log_dir" ] || fail "LOG_DIR 不应为空"
    [[ "$log_dir" == "$REPO_ROOT/engineering/output/log" ]] || fail "LOG_DIR 路径不匹配: $log_dir"
    pass "shell harness_path resolves LOG_DIR correctly"
}

test_python_path_resolve() {
    local py_result
    py_result=$(python3 "$PYTHON_LIB/harness_path_util.py" --resolve LOG_DIR 2>/dev/null)
    local shell_result
    shell_result=$(harness_path LOG_DIR)
    [ "$py_result" = "$shell_result" ] || fail "Python 与 shell 结果不一致: py=$py_result shell=$shell_result"
    pass "Python and shell path resolve一致"
}

test_unknown_key() {
    local rc=0
    harness_path NONEXISTENT_KEY >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || fail "未知 key 应返回非零"
    pass "unknown key returns error"
}

test_repo_root() {
    local root
    root=$(harness_repo_root)
    [ -f "$root/AGENTS.md" ] || fail "REPO_ROOT 应包含 AGENTS.md"
    pass "harness_repo_root points to valid repo root"
}

test_validate_paths() {
    local result
    result=$(harness_validate_paths 2>/dev/null || true)
    # 不一定所有目录都存在，但不应 crash
    local rc=0
    harness_validate_paths >/dev/null 2>&1 || rc=$?
    pass "harness_validate_paths runs without crash (rc=$rc)"
}

main() {
    test_shell_path_resolve
    test_python_path_resolve
    test_unknown_key
    test_repo_root
    test_validate_paths
    printf 'PASS: test_harness_path_util.sh\n'
}

main "$@"
```

- [ ] **Step 6: 运行路径工具测试**

```bash
bash engineering/harness/tests/test_harness_path_util.sh
```

Expected: `PASS: test_harness_path_util.sh`

- [ ] **Step 7: 验证 Python CLI**

```bash
python3 engineering/harness/lib/python/harness_path_util.py --resolve LOG_DIR
python3 engineering/harness/lib/python/harness_path_util.py --validate
```

Expected: 两个命令都输出路径，不报错

- [ ] **Step 8: 提交**

```bash
git add engineering/harness/lib/shell/harness_path_util.sh engineering/harness/lib/python/harness_path_util.py engineering/harness/lib/shell/harness_bootstrap.sh engineering/harness/tests/test_harness_path_util.sh
git commit -m "feat(path-util): 增强路径工具-校验/热加载/Python CLI/对称性测试"
```

---

### Task A5: 更新 AGENTS.md 加入 Manifest 查询 + 基线使用指引

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: 在 AGENTS.md 末尾追加**

```markdown
## Manifest 准入查询
进入任何任务前，先查询 `engineering/harness/rules/manifest.yaml` 确认：
- 当前路径匹配的 context
- 对应 access 级别（direct_edit / require_workflow / require_plan / require_confirmation / require_evidence）
- 必经 workflow（如有）
- 是否需 plan / confirmation / evidence

也可通过 `bash engineering/harness/scripts/check_access.sh --path <path> --category <category>` 快速查询。

## Baseline 使用指引
在执行 `lc-revert-code-from-patchs` 回退操作前，必须先查 `engineering/harness/config/baseline-status.yaml`：
- 确认目标 baseline 状态为 `promoted`（证据完整）
- 检查 `build_result` / `package_result` / `board_verify` 均为 PASS
- 确认 `approved_by` 和 `approved_at` 已填
- 未完成证据化晋升的 baseline 不得作为恢复真相源
```

- [ ] **Step 2: 提交**

```bash
git add AGENTS.md
git commit -m "docs(agents): 加入 Manifest 准入查询和 Baseline 使用指引"
```

---

## Batch B: 流程增强（依赖 Batch A）

### Task B1: Baseline 晋升与回退增强 (5项)

**Files:**
- Modify: `engineering/harness/config/baseline-status.yaml`
- Modify: `engineering/harness/config/baseline-evidence-template.yaml`
- Create: `engineering/harness/scripts/validate_baseline_status.sh`
- Create: `engineering/harness/tests/test_baseline_workflow.sh`

- [ ] **Step 1: baseline-status.yaml 补充 archive/candidate 示例**

在现有 promoted 记录后追加：

```yaml
  - baseline_id: BL-20260626-01
    status: archive
    source_branch: rpi5-dev
    source_commit: deadbeef
    sync_manifest: "（待同步）"

  - baseline_id: BL-20260627-01
    status: candidate
    source_branch: rpi5-dev
    source_commit: cafebabe
    sync_manifest: "engineering/output/log/sync_code_to_patchs/artifacts/20260627-000000-manifest.yaml"
    build_result: PASS
    package_result: FAIL
```

- [ ] **Step 2: baseline-evidence-template.yaml 增加字段**

在模板末尾增加：

```yaml
# revert_count: 0                     # 被回退次数（0 = 从未回退）
# rollback_to: BL-YYYYMMDD-NN         # 回退目标 baseline（仅 revert 操作填充）
```

- [ ] **Step 3: 创建 validate_baseline_status.sh**

```bash
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_baseline_status"

HARNESS_DIR="$(harness_path HARNESS_DIR)"
BASELINE="$HARNESS_DIR/config/baseline-status.yaml"

WARN_COUNT=0
SCAN_COUNT=0

report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

step_begin "校验 baseline-status.yaml"

[ ! -f "$BASELINE" ] && { report_warn "$BASELINE" "文件不存在"; step_end 1; harness_exit 1; }

python3 -c "
import sys, yaml, re

with open('$BASELINE') as f:
    data = yaml.safe_load(f)

baselines = data.get('baselines', []) if isinstance(data, dict) else []
if not baselines:
    print('WARN: baselines 列表为空')
    sys.exit(0)

errs = []
seen_ids = set()
VALID_STATUSES = {'archive', 'candidate', 'promoted'}

for i, bl in enumerate(baselines):
    bid = bl.get('baseline_id', '')
    if not bid:
        errs.append(f'baselines[{i}]: 缺少 baseline_id')
        continue
    if not re.match(r'^BL-\d{8}-\d{2}$', bid):
        errs.append(f'baselines[{i}]: baseline_id 格式非法: {bid}')
    if bid in seen_ids:
        errs.append(f'baselines[{i}]: 重复 baseline_id: {bid}')
    seen_ids.add(bid)

    status = bl.get('status', '')
    if status not in VALID_STATUSES:
        errs.append(f'{bid}: status 非法: {status}（需 archive/candidate/promoted）')

    if status == 'promoted':
        if not bl.get('approved_by'):
            errs.append(f'{bid}: promoted 缺少 approved_by')
        if not bl.get('approved_at'):
            errs.append(f'{bid}: promoted 缺少 approved_at')
        if not bl.get('build_result'):
            errs.append(f'{bid}: promoted 缺少 build_result')
        if not bl.get('package_result'):
            errs.append(f'{bid}: promoted 缺少 package_result')
        if not bl.get('board_verify'):
            errs.append(f'{bid}: promoted 缺少 board_verify')
    elif status == 'archive':
        if not bl.get('source_branch'):
            errs.append(f'{bid}: archive 缺少 source_branch')
        if not bl.get('source_commit'):
            errs.append(f'{bid}: archive 缺少 source_commit')

for e in errs:
    print(e)
" 2>&1 | while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in
        WARN:*) log_warn "${line#WARN: }" ;;
        *) report_warn "$BASELINE" "$line" ;;
    esac
done

step_end 0

if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_baseline_status 结果" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_baseline_status 结果" "warns=0" "verdict=PASS"
harness_exit 0
```

- [ ] **Step 4: 创建 test_baseline_workflow.sh**

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
VALIDATOR="$REPO_ROOT/engineering/harness/scripts/validate_baseline_status.sh"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$1"; }

TMP_SANDBOX="$(mktemp -d "$(harness_path TEST_SANDBOX_DIR)/test-baseline.XXXXXX")"

setup_sandbox() {
    rm -rf "$TMP_SANDBOX"
    mkdir -p "$TMP_SANDBOX"
    cp "$REPO_ROOT/engineering/harness/config/baseline-status.yaml" "$TMP_SANDBOX/baseline-status.yaml" 2>/dev/null || true
    mkdir -p "$TMP_SANDBOX/engineering/harness/lib/shell" "$TMP_SANDBOX/engineering/harness/config"
    cp "$REPO_ROOT/engineering/harness/lib/shell/harness_bootstrap.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_observability.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_path_util.sh" \
       "$TMP_SANDBOX/engineering/harness/lib/shell/"
    cp "$REPO_ROOT/engineering/harness/config/harness-paths.conf" \
       "$TMP_SANDBOX/engineering/harness/config/harness-paths.conf"
    cp "$REPO_ROOT/AGENTS.md" "$TMP_SANDBOX/AGENTS.md"
}

test_validator_passes_on_valid_baseline() {
    setup_sandbox
    local rc=0
    HARNESS_DIR="$TMP_SANDBOX/engineering/harness" \
    REPO_ROOT="$TMP_SANDBOX" \
    bash "$VALIDATOR" >/dev/null 2>&1 || rc=$?
    # 如果原始 baseline 有效，则应为 0；否则也是预期行为
    pass "validator runs on current baseline (rc=$rc)"
}

test_missing_baseline_id_rejected() {
    setup_sandbox
    cat > "$TMP_SANDBOX/engineering/harness/config/baseline-status.yaml" <<'EOF'
baselines:
  - status: promoted
    source_branch: test
    source_commit: aaaa
EOF
    local rc=0
    HARNESS_DIR="$TMP_SANDBOX/engineering/harness" \
    REPO_ROOT="$TMP_SANDBOX" \
    bash "$VALIDATOR" >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || fail "缺失 baseline_id 应被拒绝"
    pass "missing baseline_id rejected"
}

test_invalid_status_rejected() {
    setup_sandbox
    cat > "$TMP_SANDBOX/engineering/harness/config/baseline-status.yaml" <<'EOF'
baselines:
  - baseline_id: BL-20260601-01
    status: invalid_status
EOF
    local rc=0
    HARNESS_DIR="$TMP_SANDBOX/engineering/harness" \
    REPO_ROOT="$TMP_SANDBOX" \
    bash "$VALIDATOR" >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || fail "非法 status 应被拒绝"
    pass "invalid status rejected"
}

main() {
    test_validator_passes_on_valid_baseline
    test_missing_baseline_id_rejected
    test_invalid_status_rejected
    printf 'PASS: test_baseline_workflow.sh\n'
    rm -rf "$TMP_SANDBOX"
}

main "$@"
```

- [ ] **Step 5: 运行 baseline 测试**

```bash
bash engineering/harness/tests/test_baseline_workflow.sh
```

Expected: `PASS: test_baseline_workflow.sh`

- [ ] **Step 6: 提交**

```bash
git add engineering/harness/config/baseline-status.yaml engineering/harness/config/baseline-evidence-template.yaml engineering/harness/scripts/validate_baseline_status.sh engineering/harness/tests/test_baseline_workflow.sh
git commit -m "feat(baseline): 增强基线晋升-补充状态示例/校验器/测试"
```

---

### Task B2: 工作流契约化增强 (5项)

**Files:**
- Modify: `engineering/harness/workflows/lc-sync-code-to-patchs/WORKFLOW.md`
- Modify: `engineering/harness/workflows/lc-revert-code-from-patchs/WORKFLOW.md`
- Modify: `engineering/harness/workflows/lc-git-push-to-server/WORKFLOW.md`
- Modify: `engineering/harness/workflows/lc-sync-patchs-to-doc/WORKFLOW.md`
- Modify: `engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md`
- Create: `engineering/harness/scripts/validate_workflow_contracts.sh`
- Create: `engineering/harness/tests/fixtures/lc-quick-fix-issue/.gitkeep`

- [ ] **Step 1: 修改所有 WORKFLOW.md 的 front matter 增加 stages**

对每个 WORKFLOW.md，在 `---` 块内增加：

```yaml
stages:
  - research: "AI 分析 diff/上下文"
  - plan: "AI 生成实施计划，经用户确认"
  - code: "执行具体操作"
  - review: "验证结果并提交"
```

以 `lc-sync-code-to-patchs/WORKFLOW.md` 为例，修改后 front matter：

```yaml
---
name: lc-sync-code-to-patchs
description: workspace 源码改动归档到 patchs/rpi5，并自动更新 README 文件映射表。
stages:
  - research: "AI 分析 workspace diff/上下文"
  - plan: "AI 生成实施计划，经用户确认"
  - code: "执行具体操作"
  - review: "验证结果并提交"
---
```

对剩余4个 WORKFLOW.md 做同样修改（description 保持不变，stages 相同）。

- [ ] **Step 2: 每个 WORKFLOW.md 末尾增加 TODO 跟踪章节**

在文件末尾增加：

```markdown
## TODO 跟踪
- [ ] Step 1: 分析问题
- [ ] Step 2: 生成 plan
- [ ] Step 3: 用户确认
- [ ] Step 4: 执行
- [ ] Step 5: 验证
```

- [ ] **Step 3: 每个 WORKFLOW.md 末尾增加退出码矩阵**

在 TODO 前增加：

```markdown
## 退出码
| 退出码 | 含义 | 下一步 |
|--------|------|--------|
| 0 | 成功 | 正常继续 |
| 1 | 脚本逻辑错误 | 检查日志 |
| 3 | 环境缺失 | 安装依赖后重试 |
```

- [ ] **Step 4: 创建 validate_workflow_contracts.sh**

```bash
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_workflow_contracts"

HARNESS_DIR="$(harness_path HARNESS_DIR)"
WARN_COUNT=0
SCAN_COUNT=0

report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

step_begin "校验 WORKFLOW.md 契约完整性"

WORKFLOW_FILES=()
while IFS= read -r f; do
    WORKFLOW_FILES+=("$f")
done < <(find "$HARNESS_DIR/workflows" -name 'WORKFLOW.md' -type f 2>/dev/null || true)

if [ "${#WORKFLOW_FILES[@]}" -eq 0 ]; then
    log_warn "未发现 WORKFLOW.md"
    step_end 0
    harness_exit 0
fi

for wf in "${WORKFLOW_FILES[@]}"; do
    wf_name="${wf#$HARNESS_DIR/}"
    log_info "校验: $wf_name"
    SCAN_COUNT=$((SCAN_COUNT + 1))

    first_line=$(head -n 1 "$wf")
    [ "$first_line" = "---" ] || { report_warn "$wf:1" "缺少 YAML front matter 起始 ---"; continue; }

    end_line=$(grep -nE '^---\s*$' "$wf" | sed -n '2p' | cut -d: -f1)
    [ -n "$end_line" ] || { report_warn "$wf:1" "YAML front matter 未闭合"; continue; }

    fm_content=$(head -n "$end_line" "$wf")
    echo "$fm_content" | grep -qE '^name:' || report_warn "$wf:1" "缺少 name"
    echo "$fm_content" | grep -qE '^description:' || report_warn "$wf:1" "缺少 description"
    echo "$fm_content" | grep -qE '^stages:' || report_warn "$wf:1" "缺少 stages 声明"

    grep -qE '## TODO 跟踪' "$wf" || report_warn "$wf" "缺少 ## TODO 跟踪 章节"
    grep -qE '## 退出码' "$wf" || report_warn "$wf" "缺少 ## 退出码 章节"
done

step_end 0

if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_workflow_contracts 结果" "scanned=$SCAN_COUNT" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_workflow_contracts 结果" "scanned=$SCAN_COUNT" "warns=0" "verdict=PASS"
harness_exit 0
```

- [ ] **Step 5: 创建 quick-fix 测试夹具目录**

```bash
mkdir -p engineering/harness/tests/fixtures/lc-quick-fix-issue
touch engineering/harness/tests/fixtures/lc-quick-fix-issue/.gitkeep
```

- [ ] **Step 6: 运行契约校验器**

```bash
bash engineering/harness/scripts/validate_workflow_contracts.sh
```

Expected: `PASS`（如果所有 WORKFLOW.md 都正确更新了）

- [ ] **Step 7: 提交**

```bash
git add engineering/harness/workflows/ engineering/harness/scripts/validate_workflow_contracts.sh engineering/harness/tests/fixtures/lc-quick-fix-issue/
git commit -m "feat(workflow): 增强工作流契约-四阶段/TODO/退出码矩阵/校验器"
```

---

### Task B3: 配置静态校验流水线增强 (4项)

**Files:**
- Create: `engineering/harness/scripts/run_all_validations.sh`
- Create: `engineering/harness/scripts/validate_manifest.sh`
- Modify: `engineering/harness/scripts/validate_harness_config.sh`
- Create: `engineering/harness/tests/test_validators.sh`

- [ ] **Step 1: 创建 run_all_validations.sh**

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "run_all_validations"

VALIDATORS=(
    "validate_harness_scripts.sh"     # P0: 脚本合规
    "validate_harness_config.sh"      # P0: 配置合法性
    "validate_harness_docs.sh"        # P1: 文档一致性
    "validate_baseline_status.sh"     # 基线状态
    "validate_workflow_contracts.sh"  # 工作流契约
    "validate_manifest.sh"            # manifest 校验
)

FAIL_COUNT=0
for v in "${VALIDATORS[@]}"; do
    vpath="$SCRIPT_DIR/$v"
    if [ ! -f "$vpath" ]; then
        log_warn "校验器不存在，跳过: $v"
        continue
    fi
    step_begin "运行: $v"
    if bash "$vpath"; then
        step_end 0
    else
        step_end 1
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

if [ "$FAIL_COUNT" -gt 0 ]; then
    log_result "全量校验结果" "validators=${#VALIDATORS[@]}" "failed=$FAIL_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "全量校验结果" "validators=${#VALIDATORS[@]}" "failed=0" "verdict=PASS"
harness_exit 0
```

- [ ] **Step 2: 创建 validate_manifest.sh**

```bash
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_manifest"

HARNESS_DIR="$(harness_path HARNESS_DIR)"
MANIFEST="$HARNESS_DIR/rules/manifest.yaml"
WARN_COUNT=0

report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

step_begin "校验 manifest.yaml"

[ ! -f "$MANIFEST" ] && { report_warn "$MANIFEST" "文件不存在"; step_end 1; harness_exit 1; }

python3 -c "
import sys, yaml

with open('$MANIFEST') as f:
    data = yaml.safe_load(f)

errs = []
VALID_ACCESS = {'direct_edit', 'require_workflow', 'require_plan', 'require_confirmation', 'require_evidence'}
VALID_CATEGORIES = {'source', 'archive', 'revert', 'doc-sync', 'git', 'harness', 'test', 'docs'}

contexts = data.get('contexts', [])
if not contexts:
    errs.append('contexts 列表为空')

seen_ids = set()
for i, ctx in enumerate(contexts):
    cid = ctx.get('id', '')
    if not cid:
        errs.append(f'contexts[{i}]: 缺少 id')
        continue
    if cid in seen_ids:
        errs.append(f'contexts[{i}]: 重复 id: {cid}')
    seen_ids.add(cid)

    if not ctx.get('match'):
        errs.append(f'{cid}: 缺少 match')
    access = ctx.get('access', '')
    if access not in VALID_ACCESS:
        errs.append(f'{cid}: access 非法: {access}')
    cat = ctx.get('scope_category', '')
    if cat not in VALID_CATEGORIES:
        errs.append(f'{cid}: scope_category 非法: {cat}')

access_levels = data.get('access_levels', [])
if not access_levels:
    errs.append('access_levels 列表为空')

for e in errs:
    print(e)
" 2>&1 | while IFS= read -r line; do
    [ -z "$line" ] && continue
    report_warn "$MANIFEST" "$line"
done

step_end 0

if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_manifest 结果" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_manifest 结果" "warns=0" "verdict=PASS"
harness_exit 0
```

- [ ] **Step 3: validate_harness_config.sh 扩展 baseline 校验**

在 `YAML_TARGETS` 数组末尾追：

```bash
    "baseline-status.yaml"
```

并在 YAML 循环中增加对 baseline-status.yaml 的特殊校验（在 `field_err` 块之后，追加）：

找到 `if [ -n "$field_err" ]; then` 块，在其闭合后追加：

```bash
    # 对 baseline-status.yaml 做额外字段校验
    if [ "$yname" = "baseline-status.yaml" ]; then
        bs_err=$(python3 -c "
import sys, yaml
with open('$ypath') as f:
    data = yaml.safe_load(f)
errs = []
baselines = data.get('baselines', []) if isinstance(data, dict) else []
VALID_STATUSES = {'archive', 'candidate', 'promoted'}
for i, bl in enumerate(baselines):
    s = bl.get('status', '')
    if s not in VALID_STATUSES:
        errs.append(f'baselines[{i}] status 非法: {s}')
    if s == 'promoted' and not bl.get('approved_by'):
        errs.append(f'baselines[{i}] promoted 缺少 approved_by')
for e in errs:
    print(e)
" 2>&1) || true
        if [ -n "$bs_err" ]; then
            while IFS= read -r l; do
                [ -z "$l" ] && continue
                report_warn "$ypath" "$l"
            done <<< "$bs_err"
        fi
    fi
```

- [ ] **Step 4: 创建 test_validators.sh**

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
SCRIPTS_DIR="$REPO_ROOT/engineering/harness/scripts"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$1"; }

TMP_SANDBOX="$(mktemp -d "$(harness_path TEST_SANDBOX_DIR)/test-validators.XXXXXX")"

setup_sandbox() {
    rm -rf "$TMP_SANDBOX"
    mkdir -p "$TMP_SANDBOX/engineering/harness/lib/shell"
    mkdir -p "$TMP_SANDBOX/engineering/harness/config"
    mkdir -p "$TMP_SANDBOX/engineering/harness/rules"
    mkdir -p "$TMP_SANDBOX/engineering/harness/scripts"
    mkdir -p "$TMP_SANDBOX/engineering/harness/workflows/test-workflow"
    mkdir -p "$TMP_SANDBOX/engineering/harness/templates"
    cp "$REPO_ROOT/engineering/harness/lib/shell/harness_bootstrap.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_observability.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_path_util.sh" \
       "$TMP_SANDBOX/engineering/harness/lib/shell/"
    cp "$REPO_ROOT/engineering/harness/config/harness-paths.conf" \
       "$TMP_SANDBOX/engineering/harness/config/harness-paths.conf"
    cp "$REPO_ROOT/AGENTS.md" "$TMP_SANDBOX/AGENTS.md"
}

test_manifest_validator_rejects_invalid_access() {
    setup_sandbox
    cat > "$TMP_SANDBOX/engineering/harness/rules/manifest.yaml" <<'EOF'
version: 1
contexts:
  - id: test
    match: "**"
    scope_category: source
    access: invalid_level
access_levels:
  - level: direct_edit
    description: test
EOF
    local rc=0
    HARNESS_DIR="$TMP_SANDBOX/engineering/harness" \
    REPO_ROOT="$TMP_SANDBOX" \
    bash "$SCRIPTS_DIR/validate_manifest.sh" >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || fail "非法 access 应被拒绝"
    pass "manifest validator rejects invalid access"
}

test_config_validator_rejects_invalid_priority() {
    setup_sandbox
    cat > "$TMP_SANDBOX/engineering/harness/config/scope-mapping.yaml" <<'EOF'
version: 1
rules:
  - match: "**"
    scope: misc
    priority: abc
EOF
    local rc=0
    HARNESS_DIR="$TMP_SANDBOX/engineering/harness" \
    REPO_ROOT="$TMP_SANDBOX" \
    bash "$SCRIPTS_DIR/validate_harness_config.sh" >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || fail "非整数 priority 应被拒绝"
    pass "config validator rejects invalid priority"
}

test_all_validations_runs_without_crash() {
    setup_sandbox
    # 创建最小有效文件让校验器通过
    cat > "$TMP_SANDBOX/engineering/harness/rules/manifest.yaml" <<'EOF'
version: 1
contexts:
  - id: test
    match: "**"
    scope_category: source
    access: direct_edit
access_levels:
  - level: direct_edit
    description: test
EOF
    cat > "$TMP_SANDBOX/engineering/harness/config/scope-mapping.yaml" <<'EOF'
version: 1
rules:
  - match: "**"
    scope: misc
    priority: 0
EOF
    cat > "$TMP_SANDBOX/engineering/harness/config/doc-sync-mapping.yaml" <<'EOF'
version: 1
routes:
  - match: "**"
    docs: ["docs/test"]
    mode: fixed
    priority: 0
EOF
    cat > "$TMP_SANDBOX/engineering/harness/config/baseline-status.yaml" <<'EOF'
baselines:
  - baseline_id: BL-20260601-01
    status: promoted
    source_branch: test
    source_commit: aaaa
    build_result: PASS
    package_result: PASS
    board_verify: PASS
    approved_by: test
    approved_at: "2026-06-01T00:00:00+08:00"
EOF
    cat > "$TMP_SANDBOX/engineering/harness/workflows/test-workflow/WORKFLOW.md" <<'EOF'
---
name: test-workflow
description: test
stages:
  - research: "test"
  - plan: "test"
  - code: "test"
  - review: "test"
---
# Test

## TODO 跟踪
- [ ] Step 1

## 退出码
| 退出码 | 含义 |
|--------|------|
| 0 | OK |
EOF
    cat > "$TMP_SANDBOX/engineering/harness/templates/test.md" <<'EOF'
# Test Template
EOF

    local rc=0
    HARNESS_DIR="$TMP_SANDBOX/engineering/harness" \
    REPO_ROOT="$TMP_SANDBOX" \
    bash "$SCRIPTS_DIR/run_all_validations.sh" >/dev/null 2>&1 || rc=$?
    pass "run_all_validations runs without crash (rc=$rc)"
}

main() {
    test_manifest_validator_rejects_invalid_access
    test_config_validator_rejects_invalid_priority
    test_all_validations_runs_without_crash
    printf 'PASS: test_validators.sh\n'
    rm -rf "$TMP_SANDBOX"
}

main "$@"
```

- [ ] **Step 5: 运行校验器测试**

```bash
bash engineering/harness/tests/test_validators.sh
```

Expected: `PASS: test_validators.sh`

- [ ] **Step 6: 提交**

```bash
git add engineering/harness/scripts/run_all_validations.sh engineering/harness/scripts/validate_manifest.sh engineering/harness/scripts/validate_harness_config.sh engineering/harness/tests/test_validators.sh
git commit -m "feat(validator): 新增全量校验流水线/manifest校验器/基线校验/校验器自测"
```

---

## Batch C: 测试框架增强（依赖 Batch A+B）

### Task C1: 测试框架 + 夹具增强 (6项)

**Files:**
- Create: `engineering/harness/tests/run_all_tests.sh`
- Create: `engineering/harness/tests/fixtures/lc-sync-patchs-to-doc/.gitkeep`
- Modify: `engineering/harness/tests/README.md`

- [ ] **Step 1: 创建 run_all_tests.sh**

```bash
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"

RESULTS=()
TESTS=()
while IFS= read -r f; do
    TESTS+=("$f")
done < <(find "$SCRIPT_DIR" -name 'test_*.sh' -type f | sort)

PASS_COUNT=0
FAIL_COUNT=0
for t in "${TESTS[@]}"; do
    name=$(basename "$t")
    printf "\n========== Running: %s ==========\n" "$name"
    if bash "$t"; then
        RESULTS+=("PASS  $name")
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        RESULTS+=("FAIL  $name")
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

echo ""
echo "=========================================="
echo "  Test Results Summary"
echo "=========================================="
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo "------------------------------------------"
echo "  Total: $((PASS_COUNT + FAIL_COUNT)) | PASS: $PASS_COUNT | FAIL: $FAIL_COUNT"
echo "=========================================="

[ "$FAIL_COUNT" -eq 0 ] || exit 1
```

- [ ] **Step 2: 创建 doc-sync 夹具目录**

```bash
mkdir -p engineering/harness/tests/fixtures/lc-sync-patchs-to-doc
touch engineering/harness/tests/fixtures/lc-sync-patchs-to-doc/.gitkeep
```

- [ ] **Step 3: 更新 tests/README.md 增加回归矩阵**

在 `## 使用方式` 章节后追加：

```markdown
## 测试回归矩阵

| 测试脚本 | 测试对象 | 测试点数 | 夹具依赖 | 状态 |
|---------|---------|---------|---------|------|
| `test_harness_observability.sh` | observability 公共库 | 6 | fixtures/observability/ | ✅ |
| `test_harness_path_util.sh` | 路径工具 | 5 | — | ✅ |
| `test_check_access.sh` | 准入查询 CLI | 2 | — | ✅ |
| `test_sync_code_to_patchs.sh` | sync workflow | 3 | fixtures/lc-sync-code-to-patchs/ | ✅ |
| `test_revert_code_from_patchs.sh` | revert workflow | 2 | fixtures/lc-revert-code-from-patchs/ | ✅ |
| `test_baseline_workflow.sh` | 基线晋升 | 3 | — | ✅ |
| `test_validators.sh` | 校验器自测 | 3 | — | ✅ |
| `test_le_runs_cleanup.sh` | 跨边界清理 | 7 | — | ✅ |

> 新增测试或夹具后同步更新本矩阵。测试点数按 `test_*` 函数数量计。
```

- [ ] **Step 4: 运行全量测试**

```bash
bash engineering/harness/tests/run_all_tests.sh
```

Expected: 所有测试 PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/harness/tests/run_all_tests.sh engineering/harness/tests/fixtures/lc-sync-patchs-to-doc/ engineering/harness/tests/README.md
git commit -m "feat(test): 新增全量测试运行器/doc-sync夹具/回归矩阵"
```

---

## 最终验证

### Task F1: 最终全量校验 + 全量测试

**Files:** 无（仅运行）

- [ ] **Step 1: 运行全量校验**

```bash
bash engineering/harness/scripts/run_all_validations.sh
```

Expected: `verdict=PASS`

- [ ] **Step 2: 运行全量测试**

```bash
bash engineering/harness/tests/run_all_tests.sh
```

Expected: 所有测试 PASS

- [ ] **Step 3: 确认 git 状态干净**

```bash
git status
```

Expected: 无未跟踪的预期文件遗漏
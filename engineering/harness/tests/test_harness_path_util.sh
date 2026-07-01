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

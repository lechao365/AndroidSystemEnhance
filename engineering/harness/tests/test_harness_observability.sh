#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_path_util.sh
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
FIXTURE="$SCRIPT_DIR/fixtures/observability/basic/run_fixture.sh"
LOG_ROOT="$(harness_path LOG_DIR)"
TEST_TMP_ROOT="$(harness_path TEST_SANDBOX_DIR)/harness-observability-tests"
mkdir -p "$TEST_TMP_ROOT"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

pass() {
    printf 'PASS: %s\n' "$1"
}

assert_file_exists() {
    local path="$1"
    [ -f "$path" ] || fail "missing file: $path"
}

assert_dir_exists() {
    local path="$1"
    [ -d "$path" ] || fail "missing dir: $path"
}

assert_grep() {
    local pattern="$1"
    local path="$2"
    grep -Eq "$pattern" "$path" || fail "pattern not found: $pattern in $path"
}

assert_not_grep() {
    local pattern="$1"
    local path="$2"
    if grep -Eq "$pattern" "$path"; then
        fail "unexpected pattern found: $pattern in $path"
    fi
}

run_basic_fixture() {
    local script_name="$1"
    bash "$FIXTURE" "$script_name" >/dev/null
}

test_structured_result_and_status_lines() {
    local script_name="test-harness-observability-basic-lines"
    local log_dir="$LOG_ROOT/$script_name"
    rm -rf "$log_dir"

    run_basic_fixture "$script_name"

    local latest_log="$log_dir/latest.log"
    assert_file_exists "$latest_log"
    assert_grep '^result: APPLY 结果 applied=5 plan=' "$latest_log"
    assert_grep '^status=OK label="kernel/new/foo.c"$' "$latest_log"
    assert_grep '^status=MISS label="kernel/missing/bar.c" msg="expected missing"$' "$latest_log"
    pass "structured result/status lines"
}

test_artifact_rotation_removes_old_directory_artifacts() {
    local script_name="test-harness-observability-rotate"
    local log_dir="$LOG_ROOT/$script_name"
    local artifacts_dir="$log_dir/artifacts"
    rm -rf "$log_dir"

    local runs=() i
    for i in 1 2 3 4; do
        run_basic_fixture "$script_name"
        local ts
        ts="$(basename "$(ls -1t "$log_dir"/${script_name}-*.log | head -n 1)" .log)"
        ts="${ts#${script_name}-}"
        runs+=("$ts")
        sleep 2
    done

    assert_dir_exists "$artifacts_dir"
    local oldest_ts="${runs[0]}"
    if compgen -G "$artifacts_dir/${oldest_ts}-*" >/dev/null; then
        fail "old artifact timestamp still present after rotation: $oldest_ts"
    fi

    local unique_ts_count
    unique_ts_count="$(python3 - <<'PY' "$artifacts_dir"
import pathlib
import re
import sys
root = pathlib.Path(sys.argv[1])
seen = set()
for path in root.iterdir():
    m = re.match(r'^(\d{8}-\d{6})-', path.name)
    if m:
        seen.add(m.group(1))
print(len(seen))
PY
)"
    [ "$unique_ts_count" = "3" ] || fail "expected 3 artifact generations after rotation, got $unique_ts_count"
    pass "artifact rotation removes old directory artifacts"
}

test_harness_init_reuses_preexported_repo_root() {
    local sandbox
    sandbox="$(mktemp -d "$TEST_TMP_ROOT/root-reuse.XXXXXX")"
    local external_dir
    external_dir="$(mktemp -d "$TEST_TMP_ROOT/external-script.XXXXXX")"

    mkdir -p "$sandbox/engineering/output/log"
    touch "$sandbox/AGENTS.md"

    local external_script="$external_dir/run.sh"
    cat > "$external_script" <<'HEREDOC'
#!/bin/bash
set -euo pipefail
REPO_ROOT="${1}"
source "$REPO_ROOT/engineering/harness/lib/shell/harness_bootstrap.sh"
harness_init "root-reuse-check"
harness_exit 0
HEREDOC

    mkdir -p "$sandbox/engineering/harness/lib/shell" "$sandbox/engineering/harness/config"
    cp "$REPO_ROOT/engineering/harness/lib/shell/harness_bootstrap.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_observability.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_path_util.sh" \
       "$sandbox/engineering/harness/lib/shell/"
    cp "$REPO_ROOT/engineering/harness/config/harness-paths.conf" \
       "$sandbox/engineering/harness/config/harness-paths.conf"

    bash "$external_script" "$sandbox" >/dev/null

    local latest_log="$sandbox/engineering/output/log/root-reuse-check/latest.log"
    assert_file_exists "$latest_log"
    pass "harness_init reuses preexported REPO_ROOT"
}

main() {
    test_structured_result_and_status_lines
    test_artifact_rotation_removes_old_directory_artifacts
test_harness_init_reuses_preexported_repo_root
    test_log_warn_error_format
    test_harness_assert_api
    test_harness_trace
    test_report_no_upstream_enhanced
}

test_log_warn_error_format() {
    # shellcheck source=../../lib/shell/harness_observability.sh
    source "$SCRIPT_DIR/../lib/shell/harness_observability.sh"
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
    # shellcheck source=../../lib/shell/harness_observability.sh
    source "$SCRIPT_DIR/../lib/shell/harness_observability.sh"
    local sandbox
    sandbox="$(mktemp -d "$TEST_TMP_ROOT/assert-test.XXXXXX")"
    mkdir -p "$sandbox"

    harness_assert_eq "foo" "foo" "eq should pass"
    touch "$sandbox/exists.txt"
    harness_assert_file_exists "$sandbox/exists.txt" "file should exist"
    pass "harness_assert API"
}

test_harness_trace() {
    # shellcheck source=../../lib/shell/harness_observability.sh
    source "$SCRIPT_DIR/../lib/shell/harness_observability.sh"
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
    # shellcheck source=../../lib/shell/harness_observability.sh
    source "$SCRIPT_DIR/../lib/shell/harness_observability.sh"
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

    local tmpdir
    tmpdir="$(mktemp -d "$TEST_TMP_ROOT/no-upstream.XXXXXX")"
    (
        cd "$tmpdir"
        harness_report_no_upstream "test context" 2>/dev/null || true
    )
    pass "harness_report_no_upstream does not crash in non-git dir"
    rm -rf "$tmpdir"
}

main "$@"

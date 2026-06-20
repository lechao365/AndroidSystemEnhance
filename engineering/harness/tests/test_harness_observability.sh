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
}

main "$@"

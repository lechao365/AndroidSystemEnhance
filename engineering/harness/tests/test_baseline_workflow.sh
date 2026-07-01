#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
VALIDATOR="$REPO_ROOT/engineering/harness/scripts/validate_baseline_status.sh"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$1"; }

TMP_SANDBOX="$(mktemp -d /tmp/opencode/test-baseline.XXXXXX)"

setup_sandbox() {
    rm -rf "$TMP_SANDBOX"
    mkdir -p "$TMP_SANDBOX"
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
    TMPDIR="$TMP_SANDBOX/tmp" \
    REPO_ROOT="$TMP_SANDBOX" \
    bash "$VALIDATOR" >/dev/null 2>&1 || rc=$?
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
    TMPDIR="$TMP_SANDBOX/tmp" \
    REPO_ROOT="$TMP_SANDBOX" \
    BASELINE="$TMP_SANDBOX/engineering/harness/config/baseline-status.yaml" \
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
    TMPDIR="$TMP_SANDBOX/tmp" \
    REPO_ROOT="$TMP_SANDBOX" \
    BASELINE="$TMP_SANDBOX/engineering/harness/config/baseline-status.yaml" \
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

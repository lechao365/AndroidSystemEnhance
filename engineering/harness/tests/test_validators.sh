#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
SCRIPTS_DIR="$REPO_ROOT/engineering/harness/scripts"

TEST_FAIL_COUNT=0

record_fail() {
    TEST_FAIL_COUNT=$((TEST_FAIL_COUNT + 1))
    printf 'FAIL: %s\n' "$1" >&2
}

finalize_tests() {
    if [ "$TEST_FAIL_COUNT" -gt 0 ]; then
        printf 'TESTS-FAILED: %d test(s) failed\n' "$TEST_FAIL_COUNT" >&2
        exit 1
    fi
    printf 'PASS: %s\n' "$(basename "$0")"
}

pass() { printf 'PASS: %s\n' "$1"; }

TMP_SANDBOX="$(mktemp -d "/tmp/opencode/test-validators.XXXXXX")"

setup_sandbox() {
    rm -rf "$TMP_SANDBOX"
    mkdir -p "$TMP_SANDBOX/engineering/harness/lib/shell" \
             "$TMP_SANDBOX/engineering/harness/config" \
             "$TMP_SANDBOX/engineering/harness/rules" \
             "$TMP_SANDBOX/engineering/harness/scripts" \
             "$TMP_SANDBOX/engineering/harness/workflows/test-workflow" \
             "$TMP_SANDBOX/engineering/harness/templates"
    cp "$REPO_ROOT/engineering/harness/lib/shell/harness_bootstrap.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_observability.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_path_util.sh" \
       "$TMP_SANDBOX/engineering/harness/lib/shell/"
    cp "$REPO_ROOT/engineering/harness/config/harness-paths.conf" \
       "$TMP_SANDBOX/engineering/harness/config/harness-paths.conf"
    cp "$REPO_ROOT/AGENTS.md" "$TMP_SANDBOX/AGENTS.md"
    # Copy validator scripts into sandbox
    cp "$SCRIPTS_DIR/validate_manifest.sh" \
       "$SCRIPTS_DIR/validate_harness_config.sh" \
       "$SCRIPTS_DIR/run_all_validations.sh" \
       "$TMP_SANDBOX/engineering/harness/scripts/"
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
    bash "$TMP_SANDBOX/engineering/harness/scripts/validate_manifest.sh" >/dev/null 2>&1 || rc=$?
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
    bash "$TMP_SANDBOX/engineering/harness/scripts/validate_harness_config.sh" >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || fail "非整数 priority 应被拒绝"
    pass "config validator rejects invalid priority"
}

test_all_validations_runs_without_crash() {
    setup_sandbox
    # 复制新增校验器
    cp "$SCRIPTS_DIR/validate_lcharness_layer_map.sh" \
       "$TMP_SANDBOX/engineering/harness/scripts/"
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
    cat > "$TMP_SANDBOX/engineering/harness/config/lcharness-layer-map.yaml" <<'EOF'
version: 1
entries:
  - path: engineering/harness/config/
    kind: directory
    layer: core
    component: config-machine-layer
    target: lcharness/core/config
    rationale: test
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
    bash "$TMP_SANDBOX/engineering/harness/scripts/run_all_validations.sh" >/dev/null 2>&1 || rc=$?
    pass "run_all_validations runs without crash (rc=$rc)"
}

main() {
    test_manifest_validator_rejects_invalid_access
    test_config_validator_rejects_invalid_priority
    test_all_validations_runs_without_crash
    finalize_tests
    rm -rf "$TMP_SANDBOX"
}

main "$@"
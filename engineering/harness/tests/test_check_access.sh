#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
CHECK_ACCESS="$REPO_ROOT/engineering/harness/scripts/check_access.sh"

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

test_known_category() {
    local output
    output=$(bash "$CHECK_ACCESS" --path "~/workspace/foo" --category "source" 2>/dev/null)
    echo "$output" | grep -q '"allowed": true' || record_fail "source 应允许"
    echo "$output" | grep -q '"access": "require_evidence"' || record_fail "source 应为 require_evidence"
    pass "known category returns correct access"
}

test_unknown_category() {
    local output
    output=$(bash "$CHECK_ACCESS" --path "foo" --category "nonexistent" 2>/dev/null)
    echo "$output" | grep -q '"allowed": false' || record_fail "未知 category 应拒绝"
    pass "unknown category returns denied"
}

main() {
    test_known_category
    test_unknown_category
    finalize_tests
}

main "$@"
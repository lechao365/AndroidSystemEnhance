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
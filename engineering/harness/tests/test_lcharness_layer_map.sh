#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
VALIDATOR="$REPO_ROOT/engineering/harness/scripts/validate_lcharness_layer_map.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$1"; }

write_invalid_map() {
    cat > "$TMP_DIR/invalid.yaml" <<'YAML'
version: 1
entries:
  - path: ""
    kind: bad-kind
    layer: unknown
    component: ""
    target: ""
    rationale: ""
YAML
}

write_valid_map() {
    cat > "$TMP_DIR/valid.yaml" <<YAML
version: 1
entries:
  - path: engineering/harness/config/
    kind: directory
    layer: core
    component: config-machine-layer
    target: lcharness/core/config
    rationale: machine-readable config
YAML
}

test_validator_exists() {
    [ -x "$VALIDATOR" ] || fail "validator 不存在或不可执行"
    pass "validator exists"
}

test_invalid_map_rejected() {
    write_invalid_map
    if bash "$VALIDATOR" "$TMP_DIR/invalid.yaml" "$REPO_ROOT" >/dev/null 2>&1; then
        fail "invalid map 应被拒绝"
    fi
    pass "invalid map rejected"
}

test_valid_map_accepted() {
    write_valid_map
    bash "$VALIDATOR" "$TMP_DIR/valid.yaml" "$REPO_ROOT" >/dev/null 2>&1 || fail "valid map 应通过"
    pass "valid map accepted"
}

main() {
    test_validator_exists
    test_invalid_map_rejected
    test_valid_map_accepted
    printf 'PASS: test_lcharness_layer_map.sh\n'
}

main "$@"
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
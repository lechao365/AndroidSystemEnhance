#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_path_util.sh
source "$SCRIPT_DIR/../../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"
SYNC_SCRIPT="$REPO_ROOT/engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh"
FIXTURE_ROOT="$REPO_ROOT/engineering/harness/tests/fixtures/sync-code-to-patchs"
TMP_ROOT="$(harness_path TEST_SANDBOX_DIR)/test-sync-code-to-patchs"
PATCH_ROOT="$TMP_ROOT/repo/patchs/rpi5"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

assert_file_exists() {
    local path="$1"
    [ -f "$path" ] || fail "expected file exists: $path"
}

assert_file_not_exists() {
    local path="$1"
    [ ! -e "$path" ] || fail "expected path absent: $path"
}

assert_contains() {
    local path="$1" needle="$2"
    grep -Fq "$needle" "$path" || fail "expected '$needle' in $path"
}

assert_not_contains() {
    local path="$1" needle="$2"
    if grep -Fq "$needle" "$path"; then
        fail "did not expect '$needle' in $path"
    fi
}

reset_tmp() {
    rm -rf "$TMP_ROOT"
    mkdir -p "$TMP_ROOT/repo/engineering/harness"
    cp "$REPO_ROOT/AGENTS.md" "$TMP_ROOT/repo/AGENTS.md"
    cp -R "$REPO_ROOT/engineering/harness/." "$TMP_ROOT/repo/engineering/harness/"
    mkdir -p "$PATCH_ROOT"
}

init_git_repo() {
    local repo_path="$1" upstream_path="$2"
    git init -q "$repo_path"
    git -C "$repo_path" config user.name "Test User"
    git -C "$repo_path" config user.email "test@example.com"
    git -C "$repo_path" add .
    git -C "$repo_path" commit -q -m "base"
    git init -q --bare "$upstream_path"
    git -C "$repo_path" remote add origin "$upstream_path"
    git -C "$repo_path" push -q -u origin HEAD:main
}

run_sync() {
    local repo_root="$1"
    shift
    HOME="$TMP_ROOT/home" \
    KERNEL_WS="$repo_root/workspace/rpi5-kernel-build/common" \
    AOSP_WS="$repo_root/workspace/aosp" \
    bash "$repo_root/engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh" "$@"
}

setup_kernel_delete_fixture() {
    local scenario_root="$TMP_ROOT/repo"
    mkdir -p "$scenario_root/workspace/rpi5-kernel-build"
    cp -R "$FIXTURE_ROOT/kernel-delete/upstream" "$scenario_root/workspace/rpi5-kernel-build/common"
    cp -R "$FIXTURE_ROOT/kernel-delete/patchs/rpi5/." "$PATCH_ROOT/"
    init_git_repo "$scenario_root/workspace/rpi5-kernel-build/common" "$TMP_ROOT/kernel-delete-upstream.git"
    git -C "$scenario_root/workspace/rpi5-kernel-build/common" rm -q drivers/foo.c
}

setup_kernel_modified_new_fixture() {
    local scenario_root="$TMP_ROOT/repo"
    mkdir -p "$scenario_root/workspace/rpi5-kernel-build"
    cp -R "$FIXTURE_ROOT/kernel-modified-new/upstream" "$scenario_root/workspace/rpi5-kernel-build/common"
    init_git_repo "$scenario_root/workspace/rpi5-kernel-build/common" "$TMP_ROOT/kernel-modified-new-upstream.git"
    cp "$FIXTURE_ROOT/kernel-modified-new/workspace/drivers/keep.c" "$scenario_root/workspace/rpi5-kernel-build/common/drivers/keep.c"
    cp "$FIXTURE_ROOT/kernel-modified-new/workspace/drivers/tracked_new.c" "$scenario_root/workspace/rpi5-kernel-build/common/drivers/tracked_new.c"
    git -C "$scenario_root/workspace/rpi5-kernel-build/common" add drivers/tracked_new.c
    cp "$FIXTURE_ROOT/kernel-modified-new/workspace/drivers/untracked_new.c" "$scenario_root/workspace/rpi5-kernel-build/common/drivers/untracked_new.c"
    cp "$FIXTURE_ROOT/kernel-modified-new/workspace/drivers/temp.ko" "$scenario_root/workspace/rpi5-kernel-build/common/drivers/temp.ko"
}

setup_aosp_non_repo_fixture() {
    local scenario_root="$TMP_ROOT/repo"
    mkdir -p "$scenario_root/workspace/aosp/platform"
    cp -R "$FIXTURE_ROOT/aosp-non-repo/aosp/." "$scenario_root/workspace/aosp/"
    cp -R "$FIXTURE_ROOT/aosp-non-repo/patchs/rpi5/." "$PATCH_ROOT/"
    cp -R "$FIXTURE_ROOT/aosp-non-repo/repo-upstream" "$scenario_root/workspace/aosp/platform/build"
    init_git_repo "$scenario_root/workspace/aosp/platform/build" "$TMP_ROOT/aosp-non-repo-upstream.git"
}

case_kernel_tracked_deletion_records_manifest() {
    reset_tmp
    setup_kernel_delete_fixture
    run_sync "$TMP_ROOT/repo"

    local manifest="$PATCH_ROOT/manifest.yaml"
    local stale_patch="$PATCH_ROOT/kernel/modified/drivers/foo.c.diff"
    assert_contains "$manifest" "deletions:"
    assert_contains "$manifest" "kernel:"
    assert_contains "$manifest" "source: rpi5-kernel-build/common/drivers/foo.c"
    assert_file_not_exists "$stale_patch"
}

case_kernel_modified_and_new_sync() {
    reset_tmp
    setup_kernel_modified_new_fixture
    run_sync "$TMP_ROOT/repo"

    local modified_diff="$PATCH_ROOT/kernel/modified/drivers/keep.c.diff"
    local tracked_new="$PATCH_ROOT/kernel/new/drivers/tracked_new.c"
    local untracked_new="$PATCH_ROOT/kernel/new/drivers/untracked_new.c"
    local excluded_artifact="$PATCH_ROOT/kernel/new/drivers/temp.ko"
    local manifest="$PATCH_ROOT/manifest.yaml"

    assert_file_exists "$modified_diff"
    assert_contains "$modified_diff" "+new line"
    assert_file_exists "$tracked_new"
    assert_file_exists "$untracked_new"
    assert_file_not_exists "$excluded_artifact"
    assert_contains "$manifest" "patch: kernel/modified/drivers/keep.c.diff"
    assert_contains "$manifest" "patch: kernel/new/drivers/tracked_new.c"
    assert_contains "$manifest" "patch: kernel/new/drivers/untracked_new.c"
    assert_not_contains "$manifest" "temp.ko"
}

case_aosp_non_repo_copy_and_prune() {
    reset_tmp
    setup_aosp_non_repo_fixture
    run_sync "$TMP_ROOT/repo"

    local copied="$PATCH_ROOT/aosp/new/vendor/lechao/new.txt"
    local stale="$PATCH_ROOT/aosp/new/vendor/lechao/stale.txt"
    local manifest="$PATCH_ROOT/manifest.yaml"

    assert_file_exists "$copied"
    assert_file_not_exists "$stale"
    assert_contains "$manifest" "patch: aosp/new/vendor/lechao/new.txt"
    assert_not_contains "$manifest" "stale.txt"
}

main() {
    mkdir -p "$TMP_ROOT/home"
    case_kernel_tracked_deletion_records_manifest
    case_kernel_modified_and_new_sync
    case_aosp_non_repo_copy_and_prune
    printf 'PASS: test_sync_code_to_patchs\n'
}

main "$@"

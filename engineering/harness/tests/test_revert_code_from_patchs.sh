#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
FIXTURE_ROOT="$SCRIPT_DIR/fixtures/revert-code-from-patchs"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

assert_eq() {
    local actual="$1"
    local expected="$2"
    local message="$3"
    if [ "$actual" != "$expected" ]; then
        fail "$message (expected=$expected actual=$actual)"
    fi
}

assert_file_missing() {
    local path="$1"
    local message="$2"
    if [ -e "$path" ]; then
        fail "$message ($path)"
    fi
}

assert_grep() {
    local pattern="$1"
    local file="$2"
    local message="$3"
    if ! grep -Eq "$pattern" "$file"; then
        fail "$message ($file)"
    fi
}

assert_no_plan_entries() {
    local file="$1"
    local message="$2"
    if [ -f "$file" ] && grep -qE '^[-+]' "$file"; then
        fail "$message ($file)"
    fi
}

new_sandbox() {
    mktemp -d "/tmp/opencode/test-revert-code-from-patchs.XXXXXX"
}

copy_runtime_scaffold() {
    local sandbox="$1"
    mkdir -p \
        "$sandbox/engineering/harness/workflows/revert-code-from-patchs" \
        "$sandbox/engineering/harness/lib"
    cp "$REPO_ROOT/AGENTS.md" "$sandbox/AGENTS.md"
    cp "$REPO_ROOT/engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh" \
       "$sandbox/engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh"
    cp "$REPO_ROOT/engineering/harness/lib/harness_bootstrap.sh" \
       "$REPO_ROOT/engineering/harness/lib/harness_observability.sh" \
       "$sandbox/engineering/harness/lib/"
}

copy_fixture() {
    local fixture_name="$1"
    local sandbox="$2"
    cp -R "$FIXTURE_ROOT/$fixture_name/." "$sandbox/"
}

init_git_repo_without_upstream() {
    local repo_dir="$1"
    git -C "$repo_dir" init -q
    git -C "$repo_dir" config user.name "OpenCode Test"
    git -C "$repo_dir" config user.email "opencode@example.com"
    git -C "$repo_dir" add .
    git -C "$repo_dir" commit -qm "init"
}

run_revert_script() {
    local sandbox="$1"
    local stdout_file="$2"
    local stderr_file="$3"
    local home_dir="$4"
    local kernel_ws="$5"
    local aosp_ws="$6"
    shift 6

    set +e
    env \
        HOME="$home_dir" \
        KERNEL_WS="$kernel_ws" \
        AOSP_WS="$aosp_ws" \
        bash "$sandbox/engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh" \
        "$@" >"$stdout_file" 2>"$stderr_file"
    local rc=$?
    set -e
    return "$rc"
}

test_apply_non_repo_extra_uses_correct_path() {
    local sandbox
    sandbox="$(new_sandbox)"
    copy_runtime_scaffold "$sandbox"
    copy_fixture "non-repo-extra" "$sandbox"

    local plan_file="$sandbox/non-repo-extra.plan.tsv"
    cat > "$plan_file" <<'EOF'
# test plan
+	EXTRA-NEW-UNTRACKED	aosp:build	build/foo.txt	revert	non repo extra file
EOF

    local stdout_file="$sandbox/stdout.log"
    local stderr_file="$sandbox/stderr.log"
    local rc=0
    run_revert_script "$sandbox" "$stdout_file" "$stderr_file" \
        "$sandbox/home" \
        "$sandbox/workspace/kernel-missing" \
        "$sandbox/workspace/aosp" \
        --apply --plan-file "$plan_file" || rc=$?

    assert_eq "$rc" "0" "non-repo EXTRA apply 应成功"
    assert_file_missing "$sandbox/workspace/aosp/build/foo.txt" "non-repo EXTRA 应删除正确路径文件"
    assert_file_missing "$sandbox/workspace/aosp/build/build/foo.txt" "不应生成或依赖双前缀路径"
}

test_plan_fails_explicitly_when_upstream_missing() {
    local sandbox
    sandbox="$(new_sandbox)"
    copy_runtime_scaffold "$sandbox"
    copy_fixture "upstream-missing" "$sandbox"
    init_git_repo_without_upstream "$sandbox/workspace/kernel-tree"

    local plan_file="$sandbox/upstream-missing.plan.tsv"
    local stdout_file="$sandbox/stdout.log"
    local stderr_file="$sandbox/stderr.log"
    local rc=0
    run_revert_script "$sandbox" "$stdout_file" "$stderr_file" \
        "$sandbox/home" \
        "$sandbox/workspace/kernel-tree" \
        "$sandbox/workspace/aosp-missing" \
        --plan-file "$plan_file" || rc=$?

    assert_eq "$rc" "3" "upstream 缺失应返回环境错误退出码"
    assert_grep '无法确定 upstream base' "$stderr_file" "upstream 缺失应显式报错"
    assert_no_plan_entries "$plan_file" "upstream 缺失时不应输出部分成功 plan"
}

main() {
    test_apply_non_repo_extra_uses_correct_path
    test_plan_fails_explicitly_when_upstream_missing
    printf 'PASS: test_revert_code_from_patchs.sh\n'
}

main "$@"

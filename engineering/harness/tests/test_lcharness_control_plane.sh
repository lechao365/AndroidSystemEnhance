#!/bin/bash
set -uo pipefail

# ============================================================================
# test_lcharness_control_plane.sh — LcHarness 控制面全量测试
#
# 测试覆盖:
#   - Registry CRUD（add/remove/list/get/update/exists）
#   - Attach 流程（attach/duplicate/invalid path）
#   - Inject 流程（overlay 创建/幂等/标记文件）
#   - Validate 流程（healthy/stale/broken/detached）
#   - Reconcile 流程（healthy nop/stale fix/broken rebuild）
#   - Detach 流程（清理/未注册报错）
#
# 用法:
#   bash test_lcharness_control_plane.sh
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
CONTROL_PLANE="$REPO_ROOT/engineering/harness/control-plane"

REGISTRY_DIR="${HOME}/.local/share/lcharness"
REGISTRY_FILE="${REGISTRY_DIR}/registry.yaml"

TMP_DIR=""
TMP_REPO=""
TEST_FAIL_COUNT=0

# ============================================================================
# 辅助函数
# ============================================================================

fail() {
    TEST_FAIL_COUNT=$((TEST_FAIL_COUNT + 1))
    printf 'FAIL: %s\n' "$1" >&2
}

pass() { printf 'PASS: %s\n' "$1"; }

# 在每个 test 前调用，确保 registry 干净 + 临时目录就绪
setup_clean() {
    # 清空 registry
    rm -f "$REGISTRY_FILE" "$REGISTRY_FILE.lock"
    rm -rf "$REGISTRY_DIR/overlays"

    # 重建临时 fake repo
    rm -rf "$TMP_REPO"
    mkdir -p "$TMP_REPO/.git"
}

# 添加一个 repo 到 registry 并返回 id
# 用法: _reg_add <path> <profile>
_reg_add() {
    local path="$1" profile="$2"
    bash "$CONTROL_PLANE/lc-repo-registry.sh" add "$path" --profile "$profile" 2>/dev/null | grep -E '^[a-f0-9]{12}$' | head -1
}

# 从 registry get 输出中提取 field 值（key=value 格式）
# 用法: _reg_get_field <id> <field>
_reg_get_field() {
    local id="$1" field="$2"
    bash "$CONTROL_PLANE/lc-repo-registry.sh" get "$id" 2>/dev/null | grep -E "^${field}=" | sed "s/^${field}=//"
}

# ============================================================================
# Registry 组测试（6 个）
# ============================================================================

test_registry_add_then_get() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "test-profile")
    [ -n "$repo_id" ] || { fail "add 应返回非空 id"; return; }

    # get 检查各字段
    local path profile state overlay_root
    path=$(_reg_get_field "$repo_id" "path")
    profile=$(_reg_get_field "$repo_id" "profile")
    state=$(_reg_get_field "$repo_id" "state")
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")

    [ "$path" = "$TMP_REPO" ] || { fail "add/get path 不匹配: expected $TMP_REPO, got $path"; return; }
    [ "$profile" = "test-profile" ] || { fail "add/get profile 不匹配: expected test-profile, got $profile"; return; }
    [ "$state" = "attached" ] || { fail "add/get state 应为 attached, got $state"; return; }
    [ -n "$overlay_root" ] || { fail "overlay_root 不应为空"; return; }

    pass "registry_add_then_get"
}

test_registry_remove() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "test-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    # remove 后 list 应无此条目
    bash "$CONTROL_PLANE/lc-repo-registry.sh" remove "$repo_id" 2>/dev/null || { fail "remove 应成功"; return; }

    local list_output
    list_output=$(bash "$CONTROL_PLANE/lc-repo-registry.sh" list 2>/dev/null)
    if echo "$list_output" | grep -q "$repo_id"; then
        fail "remove 后 list 仍包含 id: $repo_id"
        return
    fi

    pass "registry_remove"
}

test_registry_list() {
    setup_clean

    local id1 id2
    id1=$(_reg_add "$TMP_REPO" "profile-a")
    [ -n "$id1" ] || { fail "add 第一个 repo 失败"; return; }

    local repo2="${TMP_DIR}/fake-repo-2"
    mkdir -p "$repo2/.git"
    id2=$(_reg_add "$repo2" "profile-b")
    [ -n "$id2" ] || { fail "add 第二个 repo 失败"; return; }

    local list_output
    list_output=$(bash "$CONTROL_PLANE/lc-repo-registry.sh" list 2>/dev/null)

    echo "$list_output" | grep -q "$id1" || { fail "list 应包含 id1: $id1"; return; }
    echo "$list_output" | grep -q "$id2" || { fail "list 应包含 id2: $id2"; return; }

    pass "registry_list"
}

test_registry_update() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "test-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    # update state
    bash "$CONTROL_PLANE/lc-repo-registry.sh" update "$repo_id" state injected 2>/dev/null || { fail "update state 应成功"; return; }

    local state
    state=$(_reg_get_field "$repo_id" "state")
    [ "$state" = "injected" ] || { fail "update 后 state 应为 injected, got $state"; return; }

    pass "registry_update"
}

test_registry_exists() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "test-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-repo-registry.sh" exists "$repo_id" 2>/dev/null || { fail "exists 应返回 0"; return; }

    bash "$CONTROL_PLANE/lc-repo-registry.sh" remove "$repo_id" 2>/dev/null || { fail "remove 应成功"; return; }

    if bash "$CONTROL_PLANE/lc-repo-registry.sh" exists "$repo_id" 2>/dev/null; then
        fail "remove 后 exists 应返回非 0"
        return
    fi

    pass "registry_exists"
}

test_registry_duplicate_path_rejected() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "test-profile")
    [ -n "$repo_id" ] || { fail "第一次 add 应成功"; return; }

    # 第二次 add 同一路径应失败
    if bash "$CONTROL_PLANE/lc-repo-registry.sh" add "$TMP_REPO" --profile "other" 2>/dev/null; then
        fail "重复 add 同一路径应失败"
        return
    fi

    pass "registry_duplicate_path_rejected"
}

# ============================================================================
# Attach 组测试（3 个）
# ============================================================================

test_attach_new_repo() {
    setup_clean

    # attach 需要交互式确认？lc-attach.sh 本身不需要交互，只有 lc-detach.sh 需要
    local attach_output
    attach_output=$(bash "$CONTROL_PLANE/lc-attach.sh" "$TMP_REPO" --profile "attach-profile" 2>&1)
    local rc=$?
    [ "$rc" -eq 0 ] || { fail "attach 应成功，rc=$rc, output: $attach_output"; return; }

    # 检查 registry 中有 entry
    local repo_id
    repo_id=$(echo "$attach_output" | grep -oE '[a-f0-9]{12}' | head -1)
    [ -n "$repo_id" ] || { fail "attach 应输出 id"; return; }

    local state
    state=$(_reg_get_field "$repo_id" "state")
    [ "$state" = "injected" ] || { fail "attach 后 state 应为 injected, got $state"; return; }

    pass "attach_new_repo"
}

test_attach_duplicate_rejected() {
    setup_clean

    bash "$CONTROL_PLANE/lc-attach.sh" "$TMP_REPO" --profile "attach-profile" 2>/dev/null || { fail "第一次 attach 应成功"; return; }

    # 第二次 attach 应失败
    if bash "$CONTROL_PLANE/lc-attach.sh" "$TMP_REPO" --profile "attach-profile" 2>/dev/null; then
        fail "重复 attach 应失败"
        return
    fi

    pass "attach_duplicate_rejected"
}

test_attach_invalid_path_rejected() {
    setup_clean

    local fake_path="${TMP_DIR}/nonexistent-path"

    if bash "$CONTROL_PLANE/lc-attach.sh" "$fake_path" --profile "test" 2>/dev/null; then
        fail "attach 不存在的路径应失败"
        return
    fi

    pass "attach_invalid_path_rejected"
}

# ============================================================================
# Inject 组测试（3 个）
# ============================================================================

test_inject_creates_overlay() {
    setup_clean

    # 先 add 到 registry（state=attached）
    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "inject-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    # 执行 inject
    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    # 获取 overlay_root
    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")
    [ -n "$overlay_root" ] || { fail "overlay_root 不应为空"; return; }

    # 检查 overlay 目录结构
    [ -d "$overlay_root" ] || { fail "overlay 目录应存在: $overlay_root"; return; }
    [ -f "${overlay_root}/.lcharness-overlay" ] || { fail "标记文件应存在"; return; }
    [ -f "${overlay_root}/.gitignore" ] || { fail ".gitignore 应存在"; return; }
    [ -d "${overlay_root}/capabilities" ] || { fail "capabilities 目录应存在"; return; }
    [ -f "${overlay_root}/capabilities/.placeholder" ] || { fail "capabilities/.placeholder 应存在"; return; }

    pass "inject_creates_overlay"
}

test_inject_idempotent() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "inject-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "第一次 inject 应成功"; return; }

    # 第二次 inject 应成功（幂等）
    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "重复 inject 应幂等"; return; }

    pass "inject_idempotent"
}

test_inject_marker_content() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "inject-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")

    # 检查标记文件 JSON 内容
    local marker_repo_id marker_profile marker_version
    marker_repo_id=$(python3 -c "import json; print(json.load(open('${overlay_root}/.lcharness-overlay')).get('repo_id',''))")
    marker_profile=$(python3 -c "import json; print(json.load(open('${overlay_root}/.lcharness-overlay')).get('profile',''))")
    marker_version=$(python3 -c "import json; print(json.load(open('${overlay_root}/.lcharness-overlay')).get('version',''))")

    [ "$marker_repo_id" = "$repo_id" ] || { fail "标记文件 repo_id 不匹配: expected $repo_id, got $marker_repo_id"; return; }
    [ "$marker_profile" = "inject-profile" ] || { fail "标记文件 profile 不匹配: expected inject-profile, got $marker_profile"; return; }
    [ "$marker_version" = "1" ] || { fail "标记文件 version 应为 1, got $marker_version"; return; }

    pass "inject_marker_content"
}

# ============================================================================
# Validate 组测试（4 个）
# ============================================================================

test_validate_healthy() {
    setup_clean

    # 完整流程：add + inject
    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "validate-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    local validate_output
    validate_output=$(bash "$CONTROL_PLANE/lc-validate.sh" "$repo_id" 2>&1)
    local rc=$?

    local status
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')

    [ "$rc" -eq 0 ] || { fail "healthy validate 应返回 rc=0, status=$status"; return; }
    [ "$status" = "healthy" ] || { fail "healthy 时 status 应为 healthy, got $status"; return; }

    pass "validate_healthy"
}

test_validate_stale() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "validate-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    # 修改标记文件中的 profile 字段制造 stale
    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")
    python3 -c "
import json
d = json.load(open('${overlay_root}/.lcharness-overlay'))
d['profile'] = 'wrong-profile'
json.dump(d, open('${overlay_root}/.lcharness-overlay', 'w'))
"

local validate_output
    validate_output=$(bash "$CONTROL_PLANE/lc-validate.sh" "$repo_id" 2>&1)
    local rc=$?

    # harness_exit 会输出日志行到 stderr（经过 2>&1 后混入 stdout）
    # 所以从 stdout 中提取 STATUS/REASON 行时需过滤掉 harness 日志前缀
    local status
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
    reason=$(echo "$validate_output" | grep -E '^REASON=' | sed 's/^REASON=//')

    [ "$rc" -ne 0 ] || { fail "stale validate 应返回非 0"; return; }
    [ "$status" = "stale" ] || { fail "stale 时 status 应为 stale, got $status"; return; }

    pass "validate_stale"
}

test_validate_broken() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "validate-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")

    # 删除 capabilities 目录制造 broken
    rm -rf "${overlay_root}/capabilities"

    local validate_output
    validate_output=$(bash "$CONTROL_PLANE/lc-validate.sh" "$repo_id" 2>&1)
    local rc=$?

    local status
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')

    [ "$rc" -ne 0 ] || { fail "broken validate 应返回非 0"; return; }
    [ "$status" = "broken" ] || { fail "broken 时 status 应为 broken, got $status"; return; }

    pass "validate_broken"
}

test_validate_not_registered() {
    setup_clean

    # validate 一个不存在的 id
    local validate_output
    validate_output=$(bash "$CONTROL_PLANE/lc-validate.sh" "nonexistent123456" 2>&1)
    local rc=$?

    local status
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')

    [ "$rc" -ne 0 ] || { fail "not_registered validate 应返回非 0"; return; }
    [ "$status" = "detached" ] || { fail "not_registered 时 status 应为 detached, got $status"; return; }

    pass "validate_not_registered"
}

# ============================================================================
# Reconcile 组测试（4 个）
# ============================================================================

test_reconcile_healthy_nop() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "reconcile-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    # healthy 时 reconcile 应成功且不做实质性改变
    bash "$CONTROL_PLANE/lc-reconcile.sh" "$repo_id" 2>/dev/null || { fail "healthy 时 reconcile 应成功"; return; }

    # 验证仍然是 healthy
    local validate_output
    validate_output=$(bash "$CONTROL_PLANE/lc-validate.sh" "$repo_id" 2>&1)
    local status
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
    [ "$status" = "healthy" ] || { fail "reconcile 后应为 healthy, got $status"; return; }

    pass "reconcile_healthy_nop"
}

test_reconcile_stale_fix() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "reconcile-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")

    # 修改标记文件 profile 制造 stale
    python3 -c "
import json
d = json.load(open('${overlay_root}/.lcharness-overlay'))
d['profile'] = 'wrong-profile'
json.dump(d, open('${overlay_root}/.lcharness-overlay', 'w'))
"

    # reconcile 应修复
    bash "$CONTROL_PLANE/lc-reconcile.sh" "$repo_id" 2>/dev/null || { fail "reconcile stale 应成功"; return; }

    # 验证修复后 healthy
    local validate_output
    validate_output=$(bash "$CONTROL_PLANE/lc-validate.sh" "$repo_id" 2>&1)
    local status
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
    [ "$status" = "healthy" ] || { fail "reconcile stale 后应为 healthy, got $status"; return; }

    pass "reconcile_stale_fix"
}

test_reconcile_broken_missing_overlay() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "reconcile-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")

    # 删除整个 overlay 目录
    rm -rf "$overlay_root"

    # reconcile 应重建
    bash "$CONTROL_PLANE/lc-reconcile.sh" "$repo_id" 2>/dev/null || { fail "reconcile missing overlay 应成功"; return; }

    # 验证修复后 overlay 存在且 healthy
    [ -d "$overlay_root" ] || { fail "reconcile 后 overlay 目录应存在"; return; }
    [ -f "${overlay_root}/.lcharness-overlay" ] || { fail "reconcile 后标记文件应存在"; return; }

    local validate_output
    validate_output=$(bash "$CONTROL_PLANE/lc-validate.sh" "$repo_id" 2>&1)
    local status
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
    [ "$status" = "healthy" ] || { fail "reconcile missing overlay 后应为 healthy, got $status"; return; }

    pass "reconcile_broken_missing_overlay"
}

test_reconcile_broken_missing_marker() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "reconcile-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")

    # 删除标记文件
    rm -f "${overlay_root}/.lcharness-overlay"

    # reconcile 应重建
    bash "$CONTROL_PLANE/lc-reconcile.sh" "$repo_id" 2>/dev/null || { fail "reconcile missing marker 应成功"; return; }

    # 验证修复后 healthy
    [ -f "${overlay_root}/.lcharness-overlay" ] || { fail "reconcile 后标记文件应存在"; return; }

    local validate_output
    validate_output=$(bash "$CONTROL_PLANE/lc-validate.sh" "$repo_id" 2>&1)
    local status
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
    [ "$status" = "healthy" ] || { fail "reconcile missing marker 后应为 healthy, got $status"; return; }

    pass "reconcile_broken_missing_marker"
}

# ============================================================================
# Detach 组测试（2 个）
# ============================================================================

test_detach_cleans_overlay() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "detach-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")

    # detach（通过 stdin 传入 "y" 确认）
    local detach_output
    detach_output=$(echo "y" | bash "$CONTROL_PLANE/lc-detach.sh" "$repo_id" 2>&1)
    local rc=$?
    [ "$rc" -eq 0 ] || { fail "detach 应成功, rc=$rc, output: $detach_output"; return; }

    # overlay 目录不应存在
    [ ! -d "$overlay_root" ] || { fail "detach 后 overlay 目录应不存在"; return; }

    # registry 条目不应存在
    if bash "$CONTROL_PLANE/lc-repo-registry.sh" exists "$repo_id" 2>/dev/null; then
        fail "detach 后 registry 条目应不存在"
        return
    fi

    pass "detach_cleans_overlay"
}

test_detach_not_registered() {
    setup_clean

    # detach 不存在的 id 应报错
    if echo "y" | bash "$CONTROL_PLANE/lc-detach.sh" "nonexistent123456" 2>/dev/null; then
        fail "detach 不存在的 id 应失败"
        return
    fi

    pass "detach_not_registered"
}

test_detach_cancelled() {
    setup_clean

    local repo_id
    repo_id=$(_reg_add "$TMP_REPO" "detach-profile")
    [ -n "$repo_id" ] || { fail "add 失败"; return; }

    bash "$CONTROL_PLANE/lc-inject.sh" "$repo_id" 2>/dev/null || { fail "inject 应成功"; return; }

    local overlay_root
    overlay_root=$(_reg_get_field "$repo_id" "overlay_root")

    # detach 但回答 "n" 取消
    local detach_output
    detach_output=$(echo "n" | bash "$CONTROL_PLANE/lc-detach.sh" "$repo_id" 2>&1)
    local rc=$?
    [ "$rc" -eq 0 ] || { fail "detach 取消应返回 0 (cancelled), rc=$rc"; return; }

    # overlay 目录应仍然存在
    [ -d "$overlay_root" ] || { fail "取消 detach 后 overlay 目录应仍然存在"; return; }

    # registry 条目应仍然存在
    bash "$CONTROL_PLANE/lc-repo-registry.sh" exists "$repo_id" 2>/dev/null || { fail "取消 detach 后 registry 条目应仍然存在"; return; }

    pass "detach_cancelled"
}

# ============================================================================
# 主入口
# ============================================================================

main() {
    TMP_DIR="$(mktemp -d)"
    TMP_REPO="$TMP_DIR/fake-repo"
    trap 'rm -rf "$TMP_DIR"' EXIT

    # 初始清理
    rm -f "$REGISTRY_FILE" "$REGISTRY_FILE.lock"
    rm -rf "$REGISTRY_DIR/overlays"

    echo "=== Registry Tests ==="
    test_registry_add_then_get
    test_registry_remove
    test_registry_list
    test_registry_update
    test_registry_exists
    test_registry_duplicate_path_rejected

    echo "=== Attach Tests ==="
    test_attach_new_repo
    test_attach_duplicate_rejected
    test_attach_invalid_path_rejected

    echo "=== Inject Tests ==="
    test_inject_creates_overlay
    test_inject_idempotent
    test_inject_marker_content

    echo "=== Validate Tests ==="
    test_validate_healthy
    test_validate_stale
    test_validate_broken
    test_validate_not_registered

    echo "=== Reconcile Tests ==="
    test_reconcile_healthy_nop
    test_reconcile_stale_fix
    test_reconcile_broken_missing_overlay
    test_reconcile_broken_missing_marker

    echo "=== Detach Tests ==="
    test_detach_cleans_overlay
    test_detach_cancelled
    test_detach_not_registered

    if [ "$TEST_FAIL_COUNT" -gt 0 ]; then
        printf 'TESTS-FAILED: %d test(s) failed\n' "$TEST_FAIL_COUNT" >&2
        exit 1
    fi

    printf 'PASS: test_lcharness_control_plane.sh (23 tests)\n'
}

main "$@"
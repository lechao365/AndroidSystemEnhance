#!/bin/bash
# test_le_runs_cleanup.sh — le_runs_cleanup.sh 单元测试
#
# 验证点:
#   1. 超过 KEEP 份时，最旧目录被删除，最新 KEEP 份保留
#   2. 散文件（probe-reboot.log 等）不被删除
#   3. 未超 KEEP 份时无操作（退出码 4）
#   4. --dry-run 模式不实际删除（退出码 4）
#   5. 无效 --keep 参数报错（退出码 3）
#   6. runs 目录不存在时正常退出
#
# 测试在沙箱目录执行，通过 monkey-patch RUNS_DIR 实现，不污染真实产物。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_path_util.sh
source "$SCRIPT_DIR/../lib/shell/harness_path_util.sh"
REPO_ROOT="$(harness_repo_root)"

CLEANUP_SCRIPT="$REPO_ROOT/engineering/harness/scripts/le_runs_cleanup.sh"
TEST_SANDBOX="$(harness_path TEST_SANDBOX_DIR)/le-runs-cleanup-tests"
rm -rf "$TEST_SANDBOX"
mkdir -p "$TEST_SANDBOX"

# 基本断言工具
fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}
pass() {
    printf 'PASS: %s\n' "$1"
}

# 构造沙箱 runs 目录，模拟真实 harness 目录结构
# 这样 bootstrap 能正确找到 REPO_ROOT（沙箱根放 AGENTS.md 锚点）
setup_sandbox() {
    local sandbox="$1"
    mkdir -p "$sandbox/engineering/harness/lib/shell"
    mkdir -p "$sandbox/engineering/harness/config"
    mkdir -p "$sandbox/engineering/output/runs"
    touch "$sandbox/AGENTS.md"

    # 复制运行时依赖（公共库 + 配置 + 被测脚本）
    cp "$REPO_ROOT/engineering/harness/lib/shell/harness_bootstrap.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_observability.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_path_util.sh" \
       "$sandbox/engineering/harness/lib/shell/"
    cp "$REPO_ROOT/engineering/harness/config/harness-paths.conf" \
       "$sandbox/engineering/harness/config/"
    cp "$CLEANUP_SCRIPT" "$sandbox/engineering/harness/scripts/" 2>/dev/null || {
        # scripts 目录可能不存在，先建
        mkdir -p "$sandbox/engineering/harness/scripts"
        cp "$CLEANUP_SCRIPT" "$sandbox/engineering/harness/scripts/"
    }

    printf '%s\n' "$sandbox"
}

# 构造 N 个 run 子目录，每个 mtime 递增 1 分钟
# 返回按 mtime 降序的目录名清单（换行分隔），供断言用
create_runs() {
    local runs_dir="$1"
    local count="$2"
    local prefix="${3:-run}"
    local base_ts
    base_ts=$(( $(date +%s) - count * 60 ))   # 保证都在过去

    local i
    for ((i=0; i<count; i++)); do
        local ts_epoch=$(( base_ts + i * 60 ))
        # 用固定时间戳格式化目录名（便于断言）
        local dir_name
        dir_name="$(date -d "@$ts_epoch" "+${prefix}-%Y%m%d-%H%M%S")-${i}"
        mkdir -p "$runs_dir/$dir_name"
        # 设置 mtime 为 ts_epoch
        touch -d "@$ts_epoch" "$runs_dir/$dir_name"
    done
}

# 运行清理脚本（在沙箱内）
run_cleanup() {
    local sandbox="$1"
    shift
    # 在沙箱中执行：脚本自带的 bootstrap 会用沙箱的 AGENTS.md 定位 REPO_ROOT
    env -i PATH="$PATH" HOME="$HOME" bash "$sandbox/engineering/harness/scripts/le_runs_cleanup.sh" "$@"
}

# 统计 runs 目录下的子目录数
count_dirs() {
    local runs_dir="$1"
    find "$runs_dir" -mindepth 1 -maxdepth 1 -type d | wc -l
}

# ========== 测试用例 ==========

test_prune_oldest_when_exceed_keep() {
    local sandbox
    sandbox="$(setup_sandbox "$TEST_SANDBOX/prune")"
    local runs_dir="$sandbox/engineering/output/runs"
    create_runs "$runs_dir" 12 "boot"

    # KEEP=10，应删除 2 个最旧的
    run_cleanup "$sandbox" --keep 10 >/dev/null 2>&1 || true

    local remaining
    remaining="$(count_dirs "$runs_dir")"
    [ "$remaining" = "10" ] || fail "期望保留 10 个，实际 $remaining"

    # 验证最旧的 2 个（编号 0、1）被删除
    [ -d "$runs_dir/boot-"*"-000000-0" ] 2>/dev/null && fail "最旧目录 0 未被删除" || true
    [ -d "$runs_dir/boot-"*"-000000-1" ] 2>/dev/null && fail "最旧目录 1 未被删除" || true

    # 验证最新的 2 个（编号 10、11）仍存在
    local latest_dir
    latest_dir="$(find "$runs_dir" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
    [ -n "$latest_dir" ] || fail "最新目录不存在"

    pass "超过 KEEP 份时清理最旧目录"
}

test_spare_files_not_deleted() {
    local sandbox
    sandbox="$(setup_sandbox "$TEST_SANDBOX/spare-files")"
    local runs_dir="$sandbox/engineering/output/runs"

    # 造 12 个目录 + 1 个散文件
    create_runs "$runs_dir" 12 "boot"
    echo "fake probe log" > "$runs_dir/probe-reboot.log"

    run_cleanup "$sandbox" --keep 10 >/dev/null 2>&1 || true

    [ -f "$runs_dir/probe-reboot.log" ] || fail "散文件 probe-reboot.log 被误删"
    pass "散文件（probe-reboot.log）不被删除"
}

test_noop_when_under_keep() {
    local sandbox
    sandbox="$(setup_sandbox "$TEST_SANDBOX/noop")"
    local runs_dir="$sandbox/engineering/output/runs"
    create_runs "$runs_dir" 5 "boot"

    local rc=0
    run_cleanup "$sandbox" --keep 10 >/dev/null 2>&1 || rc=$?
    # 退出码 4 = 无操作
    [ "$rc" = "4" ] || fail "未超 KEEP 应退出码 4，实际 $rc"

    local remaining
    remaining="$(count_dirs "$runs_dir")"
    [ "$remaining" = "5" ] || fail "5 份应全部保留，实际 $remaining"

    pass "未超 KEEP 份时无操作（退出码 4）"
}

test_dry_run_does_not_delete() {
    local sandbox
    sandbox="$(setup_sandbox "$TEST_SANDBOX/dry-run")"
    local runs_dir="$sandbox/engineering/output/runs"
    create_runs "$runs_dir" 12 "boot"

    local rc=0
    run_cleanup "$sandbox" --keep 10 --dry-run >/dev/null 2>&1 || rc=$?
    [ "$rc" = "4" ] || fail "dry-run 应退出码 4（无操作语义），实际 $rc"

    local remaining
    remaining="$(count_dirs "$runs_dir")"
    [ "$remaining" = "12" ] || fail "dry-run 不应删除，实际剩 $remaining"

    pass "--dry-run 不实际删除"
}

test_invalid_keep_exits_3() {
    local sandbox
    sandbox="$(setup_sandbox "$TEST_SANDBOX/invalid-keep")"

    # 非数字
    local rc=0
    run_cleanup "$sandbox" --keep abc >/dev/null 2>&1 || rc=$?
    [ "$rc" = "3" ] || fail "无效 --keep 应退出码 3，实际 $rc"

    pass "无效 --keep 参数退出码 3"
}

test_missing_runs_dir_exits_0() {
    local sandbox
    sandbox="$(setup_sandbox "$TEST_SANDBOX/missing-runs")"
    # 删除 runs 目录模拟不存在
    rm -rf "$sandbox/engineering/output/runs"

    local rc=0
    run_cleanup "$sandbox" --keep 10 >/dev/null 2>&1 || rc=$?
    [ "$rc" = "0" ] || fail "runs 目录不存在应退出码 0（非错误），实际 $rc"

    pass "runs 目录不存在正常退出（退出码 0）"
}

test_keep_zero_exits_3() {
    local sandbox
    sandbox="$(setup_sandbox "$TEST_SANDBOX/keep-zero")"

    local rc=0
    run_cleanup "$sandbox" --keep 0 >/dev/null 2>&1 || rc=$?
    [ "$rc" = "3" ] || fail "--keep 0 应退出码 3，实际 $rc"

    pass "--keep 0 退出码 3"
}

# ========== 主入口 ==========

main() {
    test_prune_oldest_when_exceed_keep
    test_spare_files_not_deleted
    test_noop_when_under_keep
    test_dry_run_does_not_delete
    test_invalid_keep_exits_3
    test_missing_runs_dir_exits_0
    test_keep_zero_exits_3

    printf '\n全部通过 ✅\n'
}

main "$@"

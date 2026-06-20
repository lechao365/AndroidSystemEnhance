#!/bin/bash
# le_runs_cleanup.sh — 清理 LE 框架 runs/ 下过期 run-id 子目录
#
# 规则:
#   - 仅清理子目录（run-id 目录），散文件（如 probe-reboot.log）保留不动
#   - 按目录 mtime 降序排列，保留最新 N 份（默认 20），删除其余
#   - 保留份数: --keep N > 环境变量 LE_RUNS_KEEP > 默认 20
#
# 退出码 (OBS 标准):
#   0 = 成功（含无操作场景）
#   1 = 通用失败
#   3 = 参数/环境错误
#   4 = 无操作（无需清理）
#
# 用法:
#   bash le_runs_cleanup.sh                       # 使用默认/环境变量保留份数
#   bash le_runs_cleanup.sh --keep 20             # 指定保留份数
#   bash le_runs_cleanup.sh --keep 20 --dry-run   # 试运行，仅打印不删除
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "le-runs-cleanup"

# ---------- 参数解析 ----------
KEEP="${LE_RUNS_KEEP:-20}"
DRY_RUN=false

while [ $# -gt 0 ]; do
    case "$1" in
        --keep)
            [ $# -ge 2 ] || { log_error "--keep 需要参数值"; harness_exit 3; }
            KEEP="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            cat <<EOF
用法: bash le_runs_cleanup.sh [--keep N] [--dry-run]
  --keep N      保留最新 N 份 run 目录（默认: \${LE_RUNS_KEEP:-20}）
  --dry-run     试运行，仅打印将删除的目录，不实际删除
EOF
            harness_exit 0
            ;;
        *)
            log_error "未知参数: $1"
            harness_exit 3
            ;;
    esac
done

# 校验 KEEP 为正整数
case "$KEEP" in
    ''|*[!0-9]*)
        log_error "--keep 必须为正整数，当前值: '$KEEP'"
        harness_exit 3
        ;;
esac
[ "$KEEP" -ge 1 ] || { log_error "--keep 必须 >= 1"; harness_exit 3; }

# ---------- 路径解析（PATH-001 合规） ----------
RUNS_DIR="$(harness_path RUNS_DIR)"

# ---------- 主逻辑 ----------
step_begin "扫描 runs 目录 (保留 ${KEEP} 份, dry_run=${DRY_RUN})"
log_info "runs 目录: $RUNS_DIR"

if [ ! -d "$RUNS_DIR" ]; then
    log_warn "runs 目录不存在，视为无操作: $RUNS_DIR"
    step_end 0
    harness_exit 0
fi

# 收集子目录，按 mtime 降序（%T@ 为 epoch 浮点，sort -rn 降序）
mapfile -t DIRS < <(find "$RUNS_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@\t%p\n' \
                    | sort -rn | cut -f2-)
step_end 0

TOTAL=${#DIRS[@]}

if [ "$TOTAL" -le "$KEEP" ]; then
    log_info "当前 ${TOTAL} 份 ≤ ${KEEP}，无需清理"
    harness_exit 4
fi

PRUNE_COUNT=$((TOTAL - KEEP))
log_info "发现 ${TOTAL} 份 run 目录，将清理最旧 ${PRUNE_COUNT} 份"

step_begin "删除 ${PRUNE_COUNT} 个过期 run 目录"
PRUNED=0
FAILED=0
for ((i=KEEP; i<TOTAL; i++)); do
    d="${DIRS[i]}"
    if $DRY_RUN; then
        log_warn "[dry-run] 将删除: $d"
    else
        if rm -rf -- "$d"; then
            log_info "已删除: $d"
            PRUNED=$((PRUNED + 1))
        else
            log_error "删除失败: $d"
            FAILED=$((FAILED + 1))
        fi
    fi
done
step_end 0

log_result "runs 清理完成" \
    "keep=${KEEP}" \
    "total_before=${TOTAL}" \
    "pruned=${PRUNED}" \
    "failed=${FAILED}" \
    "dry_run=${DRY_RUN}"

# dry-run 视为无操作退出码
if $DRY_RUN; then
    harness_exit 4
fi

# 有删除失败 → 通用失败；否则成功
if [ "$FAILED" -gt 0 ]; then
    harness_exit 1
fi
harness_exit 0

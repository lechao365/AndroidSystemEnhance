#!/bin/bash
# ============================================================================
# apply_preset_bugs.sh — 向 workspace 注入 3 个预设 bug，验证 AI 闭环能力
#
# 用法:
#   apply_preset_bugs.sh --bug 1          仅 Bug 1
#   apply_preset_bugs.sh --bug 1,2,3      全部 3 个
#   apply_preset_bugs.sh --revert         回滚所有 bug
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"
harness_init --with-errexit "apply_preset_bugs"

AOSP_WS="${AOSP_WS:-$(harness_env_path ENV_AOSP_WS)}"
LCIOD_HAL="${AOSP_WS}/vendor/lechao/services/lechao_lciod/hal/hal_service.cpp"
LCIOD_DAEMON="${AOSP_WS}/vendor/lechao/services/lechao_lciod/daemon/service.cpp"
LCIOD_DEVIO="${AOSP_WS}/vendor/lechao/services/lechao_lciod/hal/device_io.cpp"
BACKUP_DIR="${AOSP_WS}/.lciod_bug_backup_$(date +%Y%m%d%H%M%S)"

apply_bug() {
    local bug_num="$1"
    step_begin "apply_bug_${bug_num}"
    case "$bug_num" in
        1)
            log_info "Bug 1: HAL getStats read_bytes/write_bytes 字段反转"
            cp "$LCIOD_HAL" "$BACKUP_DIR/hal_service.cpp.bak"
            sed -i 's/_aidl_return->readBytes = raw\.read_bytes;/_aidl_return->readBytes = raw.TEMP_placeholder;/' "$LCIOD_HAL"
            sed -i 's/_aidl_return->writeBytes = raw\.write_bytes;/_aidl_return->writeBytes = raw.read_bytes;/' "$LCIOD_HAL"
            sed -i 's/_aidl_return->readBytes = raw\.TEMP_placeholder;/_aidl_return->readBytes = raw.write_bytes;/' "$LCIOD_HAL"
            log_info "Bug 1 applied: hal_service.cpp readBytes/writeBytes reversed"
            ;;
        2)
            log_info "Bug 2: Daemon getAverageRate 公式分子分母颠倒"
            cp "$LCIOD_DAEMON" "$BACKUP_DIR/service.cpp.bak"
            sed -i 's/_aidl_return = static_cast<int64_t>(total \* 1000000000ULL \/ totalNs);/_aidl_return = static_cast<int64_t>(totalNs * 1000000000ULL \/ total);/' "$LCIOD_DAEMON"
            log_info "Bug 2 applied: service.cpp getAverageRate formula reversed"
            ;;
        3)
            log_info "Bug 3: HAL readEvent 排空循环移除——只读一次"
            cp "$LCIOD_DEVIO" "$BACKUP_DIR/device_io.cpp.bak"
            sed -i ':a;N;$!ba;s/while ((n = read(fd, &tmp, sizeof(tmp))) == (ssize_t)sizeof(tmp)) {\n        \*event = tmp;\n        count++;\n        ret = poll(&pfd, 1, 0);\n        if (ret <= 0)\n            break;\n    }/n = read(fd, \&tmp, sizeof(tmp));\n    if (n == (ssize_t)sizeof(tmp)) {\n        *event = tmp;\n        count = 1;\n    }/' "$LCIOD_DEVIO"
            log_info "Bug 3 applied: device_io.cpp read_event drain loop removed"
            ;;
        *)
            log_error "Unknown bug number: $bug_num (valid: 1,2,3)"
            step_end 1
            return 1
            ;;
    esac
    step_end "apply_bug_${bug_num}"
}

revert_bugs() {
    step_begin "revert_bugs"
    if [[ -z "${BACKUP_DIR:-}" ]] || [[ ! -d "$BACKUP_DIR" ]]; then
        log_error "No backup directory found. Cannot revert."
        return 1
    fi
    [[ -f "$BACKUP_DIR/hal_service.cpp.bak" ]] && cp "$BACKUP_DIR/hal_service.cpp.bak" "$LCIOD_HAL"
    [[ -f "$BACKUP_DIR/service.cpp.bak" ]] && cp "$BACKUP_DIR/service.cpp.bak" "$LCIOD_DAEMON"
    [[ -f "$BACKUP_DIR/device_io.cpp.bak" ]] && cp "$BACKUP_DIR/device_io.cpp.bak" "$LCIOD_DEVIO"
    log_info "Reverted all bugs from $BACKUP_DIR"
    step_end "revert_bugs"
}

BUGS=""
DO_REVERT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bug) BUGS="$2"; shift 2 ;;
        --revert) DO_REVERT=true; shift ;;
        *) log_error "Unknown arg: $1"; harness_exit 3 ;;
    esac
done

if $DO_REVERT; then
    revert_bugs
    harness_exit 0
fi

if [[ -z "$BUGS" ]]; then
    log_error "--bug is required (e.g. --bug 1 or --bug 1,2,3)"
    harness_exit 3
fi

mkdir -p "$BACKUP_DIR"
log_info "Backup directory: $BACKUP_DIR"

IFS=',' read -ra BUG_ARRAY <<< "$BUGS"
for b in "${BUG_ARRAY[@]}"; do
    b_trimmed="$(echo "$b" | xargs)"
    apply_bug "$b_trimmed"
done

log_info "Bugs applied. To revert: $0 --revert"
harness_exit 0

#!/bin/bash
set -uo pipefail

# ============================================================================
# sync_patchs_to_doc.sh — patchs/rpi5 变动报告生成器
# 规则详见: skills/sync-patchs-to-doc/SKILL.md
# 用法:    bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh [--check-only]
# ============================================================================

# --- Configuration ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PATCH_DIR="patchs/rpi5"

# --- Colors -----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}========== $1 ==========${NC}"; }

# ============================================================================
# 参数解析
# ============================================================================
CHECK_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --check-only|--dry-run) CHECK_ONLY=true ;;
        -h|--help)
            echo "Usage: bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh [--check-only]"
            echo "  --check-only  仅输出报告，不输出 AI 操作提示"
            exit 0 ;;
        *) log_error "未知参数: $arg"; exit 1 ;;
    esac
done

# ============================================================================
# 前置检查
# ============================================================================
cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; exit 1; }

if [ ! -d "$PATCH_DIR" ]; then
    log_error "patchs 目录不存在: $PATCH_DIR"
    exit 1
fi

HEAD_SHORT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
log_info "基准: HEAD ($HEAD_SHORT)"
log_info "扫描: $PATCH_DIR/"

# ============================================================================
# 获取变动列表
# ============================================================================
# --name-status 输出格式: <status>\t<old_path>\t<new_path> (仅 R/C 有三列)
# A=新增, M=修改, D=删除, R=重命名, C=复制
DIFF_OUTPUT=$(git diff HEAD --name-status -- "$PATCH_DIR" 2>/dev/null)

if [ -z "$DIFF_OUTPUT" ]; then
    echo ""
    log_info "无变动"
    exit 0
fi

# ============================================================================
# 按目录分组输出
# ============================================================================
log_step "Patchs → Doc 变动报告"

# 统计
TOTAL_A=0
TOTAL_M=0
TOTAL_D=0
TOTAL_R=0
TOTAL_OTHER=0

# 按顶级分组（kernel/new, kernel/modified, aosp/new, aosp/modified, others）
# 使用普通变量而非关联数组，避免 set -u 兼容性问题
GROUP_kernel_modified=""
GROUP_kernel_new=""
GROUP_aosp_modified=""
GROUP_aosp_new=""
GROUP_others=""
GROUP_root=""

while IFS=$'\t' read -r status path1 path2; do
    # 去除 status 可能的数字后缀（如 R100, C75）
    base_status="${status:0:1}"

    # 确定显示路径和实际路径
    case "$base_status" in
        R|C) display_path="$path2"; stat_path="$path2" ;;
        *)   display_path="$path1"; stat_path="$path1" ;;
    esac

    # 路径已包含 patchs/rpi5/ 前缀，去除用于分组
    rel="${display_path#$PATCH_DIR/}"

    # 提取顶级分组：kernel/modified, kernel/new, aosp/modified, aosp/new, others
    if [[ "$rel" == kernel/modified/* ]]; then
        group="kernel_modified"
    elif [[ "$rel" == kernel/new/* ]]; then
        group="kernel_new"
    elif [[ "$rel" == aosp/modified/* ]]; then
        group="aosp_modified"
    elif [[ "$rel" == aosp/new/* ]]; then
        group="aosp_new"
    elif [[ "$rel" == others/* ]]; then
        group="others"
    else
        group="root"
    fi

    # 追加到分组
    case "$base_status" in
        A) TOTAL_A=$((TOTAL_A + 1)) ;;
        M) TOTAL_M=$((TOTAL_M + 1)) ;;
        D) TOTAL_D=$((TOTAL_D + 1)) ;;
        R) TOTAL_R=$((TOTAL_R + 1)) ;;
        *) TOTAL_OTHER=$((TOTAL_OTHER + 1)) ;;
    esac

    # 获取行数统计
    numstat=$(git diff HEAD --numstat -- "$display_path" 2>/dev/null | head -1)
    added=$(echo "$numstat" | awk '{print $1}')
    deleted=$(echo "$numstat" | awk '{print $2}')
    [ "$added" = "-" ] && added="0"
    [ "$deleted" = "-" ] && deleted="0"

    # 重命名显示 old → new
    case "$base_status" in
        R)
            old_rel="${path1#$PATCH_DIR/}"
            line="  [R] ${old_rel} → ${rel}  +${added} -${deleted}"
            ;;
        C)
            line="  [C] ${rel}  +${added} -${deleted}"
            ;;
        A)
            line="  [A] ${rel}  +${added} -${deleted}"
            ;;
        M)
            line="  [M] ${rel}  +${added} -${deleted}"
            ;;
        D)
            line="  [D] ${rel}  -${deleted}"
            ;;
        *)
            line="  [${base_status}] ${rel}  +${added} -${deleted}"
            ;;
    esac

    # 追加到对应分组变量
    case "$group" in
        kernel_modified) GROUP_kernel_modified+="$line"$'\n' ;;
        kernel_new)      GROUP_kernel_new+="$line"$'\n' ;;
        aosp_modified)   GROUP_aosp_modified+="$line"$'\n' ;;
        aosp_new)        GROUP_aosp_new+="$line"$'\n' ;;
        others)          GROUP_others+="$line"$'\n' ;;
        root)            GROUP_root+="$line"$'\n' ;;
    esac

done <<< "$DIFF_OUTPUT"

# 按固定顺序输出分组
output_group() {
    local label="$1" content="$2"
    [ -z "$content" ] && return
    echo ""
    echo "--- $label/ ---"
    echo -ne "$content" | grep -v '^$' || true
}

output_group "kernel/modified" "$GROUP_kernel_modified"
output_group "kernel/new"      "$GROUP_kernel_new"
output_group "aosp/modified"   "$GROUP_aosp_modified"
output_group "aosp/new"        "$GROUP_aosp_new"
output_group "others"          "$GROUP_others"
output_group "(root)"          "$GROUP_root"

# ============================================================================
# 汇总
# ============================================================================
echo ""
TOTAL=$((TOTAL_A + TOTAL_M + TOTAL_D + TOTAL_R + TOTAL_OTHER))
echo "总计: $TOTAL 个文件变动 ($([ $TOTAL_A -gt 0 ] && echo -n "$TOTAL_A 新增, ")$([ $TOTAL_M -gt 0 ] && echo -n "$TOTAL_M 修改, ")$([ $TOTAL_D -gt 0 ] && echo -n "$TOTAL_D 删除")$([ $TOTAL_R -gt 0 ] && echo -n ", $TOTAL_R 重命名")$([ $TOTAL_OTHER -gt 0 ] && echo -n ", $TOTAL_OTHER 其他"))"

# ============================================================================
# AI 操作提示
# ============================================================================
if [ "$CHECK_ONLY" = false ]; then
    cat <<TIP

下一步: AI 根据以上变动报告，判断影响的技术文档范围，给出更新方案。
        经用户确认后，执行文档落盘。
TIP
fi

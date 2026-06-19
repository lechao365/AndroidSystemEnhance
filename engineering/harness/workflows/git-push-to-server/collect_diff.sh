#!/bin/bash
set -uo pipefail

# ============================================================================
# collect_diff.sh — 收集 git status + diff + 分支信息，格式化输出给 AI
# 规则详见: engineering/harness/workflows/git-push-to-server/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/git-push-to-server/collect_diff.sh [--stat-only]
# 退出码:  0=有改动（正常输出）; 3=参数/环境错误; 4=无改动（输出 nothing to commit）
# ============================================================================

# --- 锚点查找 REPO_ROOT -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 3; }

# --- 接入维测库 -------------------------------------------------------------
# shellcheck source=../../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

harness_init "collect_diff"

# --- Configuration ----------------------------------------------------------
# diff 过大阈值
MAX_FILES=50
MAX_LINES=5000

# 空仓库兜底：UNTRACKED 仅在 HEAD 存在分支赋值，空仓库场景需预定义
UNTRACKED=""

# ============================================================================
# 参数解析
# ============================================================================
STAT_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --stat-only) STAT_ONLY=true ;;
        -h|--help)
            echo "Usage: bash engineering/harness/workflows/git-push-to-server/collect_diff.sh [--stat-only]"
            echo "  --stat-only  只输出分支 + status + --stat，不输出 diff 正文"
            exit 0 ;;
        *) log_error "未知参数: $arg"; harness_exit 3 ;;
    esac
done

# ============================================================================
# 前置检查
# ============================================================================
cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; harness_exit 3; }

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
REMOTE_NAME=$(git config --get branch."${CURRENT_BRANCH}".remote 2>/dev/null || echo "origin")
REMOTE_URL=$(git remote get-url "$REMOTE_NAME" 2>/dev/null || echo "unknown")

# HEAD 存在性检查（空仓库兜底）
HEAD_SHORT=$(git rev-parse --short HEAD 2>/dev/null || echo "none")

# ============================================================================
# 收集改动（含 staged + unstaged + untracked）
# ============================================================================
# git status --porcelain 覆盖所有三种状态，空输出 = 无改动
STATUS_OUTPUT=$(git status --porcelain 2>/dev/null)

if [ -z "$STATUS_OUTPUT" ]; then
    echo "nothing to commit, working tree clean"
    log_info "无改动，退出码 4"
    harness_exit 4
fi

# 文件数统计
FILE_COUNT=$(echo "$STATUS_OUTPUT" | grep -c '.' || true)

# ============================================================================
# 输出格式化
# ============================================================================
step_begin "收集 git push 上下文"
echo "当前分支: $CURRENT_BRANCH"
echo "远程:     $REMOTE_NAME ($REMOTE_URL)"
echo ""

# --- 改动文件（git status）---
echo "========== 改动文件 (git status) =========="
echo "$STATUS_OUTPUT"
echo ""

# --- 改动统计 (--stat) ---
echo "========== 改动统计 (--stat) =========="
# HEAD 可能不存在（空仓库），用双保险：优先 HEAD，失败则用空树
if [ "$HEAD_SHORT" = "none" ]; then
    # 空仓库：所有文件都是新增，用 ls-files + status 拼 stat
    git status --porcelain | cut -c4- | sed 's/^/ /' || true
    echo "(空仓库，无法生成 stat)"
else
    # tracked 改动 stat
    git --no-pager diff HEAD --stat 2>/dev/null || true
    # untracked 文件不在 diff HEAD 里，单独列行数
    UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null)
    if [ -n "$UNTRACKED" ]; then
        echo "--- untracked (新增, 未追踪) ---"
        echo "$UNTRACKED" | while IFS= read -r f; do
            [ -z "$f" ] && continue
            lines=$(wc -l < "$f" 2>/dev/null || echo "?")
            echo " $f | $lines 行 (全新文件)"
        done
    fi
fi
echo ""

# --- diff 正文 ---
if [ "$STAT_ONLY" = true ]; then
    echo "========== DIFF 内容 (--stat-only 模式，跳过) =========="
    echo "(已跳过 diff 正文)"
else
    # 判断 diff 规模
    DIFF_LINES=0
    if [ "$HEAD_SHORT" != "none" ]; then
        DIFF_LINES=$(git --no-pager diff HEAD 2>/dev/null | wc -l || echo 0)
    fi
    # 加上 untracked 文件的总行数
    if [ -n "$UNTRACKED" ]; then
        UNTRACKED_LINES=$(echo "$UNTRACKED" | while IFS= read -r f; do
            [ -z "$f" ] && continue
            wc -l < "$f" 2>/dev/null || echo 0
        done | awk '{s+=$1} END {print s+0}')
        DIFF_LINES=$((DIFF_LINES + UNTRACKED_LINES))
    fi

    if [ "$FILE_COUNT" -gt "$MAX_FILES" ] || [ "$DIFF_LINES" -gt "$MAX_LINES" ]; then
        # 大 diff 降级：每文件前 20 行
        echo "========== DIFF 内容 (已降级: $FILE_COUNT 文件 / $DIFF_LINES 行) =========="
        echo "⚠ diff 已截断（超过阈值 $MAX_FILES 文件或 $MAX_LINES 行），AI 基于 stat + 文件摘要生成 message"
        echo ""
        # tracked 改动：每文件前 20 行
        if [ "$HEAD_SHORT" != "none" ]; then
            CHANGED_FILES=$(git --no-pager diff HEAD --name-only 2>/dev/null)
            echo "$CHANGED_FILES" | while IFS= read -r f; do
                [ -z "$f" ] && continue
                echo "--- $f (前 20 行) ---"
                git --no-pager diff HEAD -- "$f" 2>/dev/null | head -20 || true
                echo ""
            done
        fi
        # untracked 文件：前 20 行内容
        if [ -n "$UNTRACKED" ]; then
            echo "$UNTRACKED" | while IFS= read -r f; do
                [ -z "$f" ] && continue
                echo "--- $f (新文件, 前 20 行) ---"
                head -20 "$f" 2>/dev/null || echo "(无法读取)"
                echo ""
            done
        fi
    else
        # 完整 diff
        echo "========== DIFF 内容 =========="
        if [ "$HEAD_SHORT" != "none" ]; then
            git --no-pager diff HEAD 2>/dev/null || true
        fi
        # untracked 文件完整内容
        if [ -n "$UNTRACKED" ]; then
            echo "--- untracked 新文件完整内容 ---"
            echo "$UNTRACKED" | while IFS= read -r f; do
                [ -z "$f" ] && continue
                echo "+++ b/$f (新文件)"
                sed 's/^/+/' "$f" 2>/dev/null || echo "(无法读取)"
                echo ""
            done
        fi
    fi
fi
echo "======================================"
step_end 0
harness_exit 0

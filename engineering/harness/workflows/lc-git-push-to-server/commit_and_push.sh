#!/bin/bash
set -uo pipefail

# ============================================================================
# commit_and_push.sh — git add -A + commit -F + push，失败保留 commit
# 规则详见: engineering/harness/workflows/lc-git-push-to-server/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/lc-git-push-to-server/commit_and_push.sh \
#              --message-file <path> [--branch <b>] [--remote origin] [--no-push]
# 退出码:  0=成功; 1=通用失败; 2=push失败(commit已保留); 3=参数/环境错误; 4=无改动可提交
# ============================================================================

# --- 锚点 + 公共库（bootstrap 统一入口）-------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"

harness_init "commit_and_push"

# ============================================================================
# 参数解析
# ============================================================================
MESSAGE_FILE=""
BRANCH=""
REMOTE="origin"
NO_PUSH=false

while [ $# -gt 0 ]; do
    case "$1" in
        --message-file)
            [ $# -lt 2 ] && { log_error "--message-file 需要参数"; harness_exit 3; }
            MESSAGE_FILE="$2"; shift 2 ;;
        --branch)
            [ $# -lt 2 ] && { log_error "--branch 需要参数"; harness_exit 3; }
            BRANCH="$2"; shift 2 ;;
        --remote)
            [ $# -lt 2 ] && { log_error "--remote 需要参数"; harness_exit 3; }
            REMOTE="$2"; shift 2 ;;
        --no-push)
            NO_PUSH=true; shift ;;
        -h|--help)
            echo "Usage: bash engineering/harness/workflows/lc-git-push-to-server/commit_and_push.sh --message-file <path> [--branch <b>] [--remote origin] [--no-push]"
            echo "  --message-file <path>  message 文本文件（git commit -F 读取）"
            echo "  --branch <b>           推送分支（默认当前分支）"
            echo "  --remote <name>        远程名（默认 origin）"
            echo "  --no-push              只 commit 不 push"
            harness_exit 0 ;;
        *) log_error "未知参数: $1"; harness_exit 3 ;;
    esac
done

# ============================================================================
# 校验
# ============================================================================
cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; harness_exit 3; }

if [ -z "$MESSAGE_FILE" ]; then
    log_error "缺少必填参数 --message-file"
    harness_exit 3
fi
if [ ! -f "$MESSAGE_FILE" ]; then
    log_error "message 文件不存在: $MESSAGE_FILE"
    harness_exit 3
fi
if [ ! -s "$MESSAGE_FILE" ]; then
    log_error "message 文件为空: $MESSAGE_FILE"
    harness_exit 3
fi

# 分支默认值：当前分支
if [ -z "$BRANCH" ]; then
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
        log_error "无法确定当前分支，请用 --branch 显式指定"
        harness_exit 3
    fi
fi

# ============================================================================
# Step 1: git add -A
# ============================================================================
step_begin "Step 1: 暂存所有改动"
git add -A || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?

STAGED_COUNT=$(git diff --cached --name-only 2>/dev/null | grep -c '.' || true)
if [ "$STAGED_COUNT" -eq 0 ]; then
    log_error "无改动可提交（git add -A 后暂存区为空）"
    harness_exit 4
fi
log_info "已暂存 $STAGED_COUNT 个文件"
step_end 0

# ============================================================================
# Step 2: git commit -F
# ============================================================================
step_begin "Step 2: 提交"
git commit -F "$MESSAGE_FILE" || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
log_info "提交成功: $COMMIT_HASH"
log_result "COMMIT 结果" "commit=$COMMIT_HASH" "branch=$BRANCH" "staged=$STAGED_COUNT"
step_end 0

# ============================================================================
# Step 3: git push（可选）
# ============================================================================
if [ "$NO_PUSH" = true ]; then
    log_info "--no-push 模式，跳过推送"
    step_begin "完成（未推送）"
    log_result "PUSH 结果" "pushed=false" "commit=$COMMIT_HASH" "branch=$BRANCH"
    step_end 0
    harness_exit 0
fi

step_begin "Step 3: 推送"
log_info "目标: $REMOTE/$BRANCH"
log_warn "push 失败时 commit 已保留（本地 $COMMIT_HASH），未自动回退"
git push "$REMOTE" "$BRANCH" || on_err --exit-code 2 "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
REMOTE_URL=$(git remote get-url "$REMOTE" 2>/dev/null || echo "unknown")
log_info "推送成功: $REMOTE/$BRANCH ($REMOTE_URL)"
log_result "PUSH 结果" "pushed=true" "commit=$COMMIT_HASH" "remote=$REMOTE" "branch=$BRANCH" "remote_url=$REMOTE_URL"
step_end 0

harness_exit 0

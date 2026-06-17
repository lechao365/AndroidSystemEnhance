#!/bin/bash
set -uo pipefail

# ============================================================================
# commit_and_push.sh — git add -A + commit -F + push，失败保留 commit
# 规则详见: skills/git-push-to-server/SKILL.md
# 用法:    bash skills/git-push-to-server/commit_and_push.sh \
#              --message-file <path> [--branch <b>] [--remote origin] [--no-push]
# ============================================================================

# --- Configuration ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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
MESSAGE_FILE=""
BRANCH=""
REMOTE="origin"
NO_PUSH=false

while [ $# -gt 0 ]; do
    case "$1" in
        --message-file)
            [ $# -lt 2 ] && { log_error "--message-file 需要参数"; exit 1; }
            MESSAGE_FILE="$2"; shift 2 ;;
        --branch)
            [ $# -lt 2 ] && { log_error "--branch 需要参数"; exit 1; }
            BRANCH="$2"; shift 2 ;;
        --remote)
            [ $# -lt 2 ] && { log_error "--remote 需要参数"; exit 1; }
            REMOTE="$2"; shift 2 ;;
        --no-push)
            NO_PUSH=true; shift ;;
        -h|--help)
            echo "Usage: bash skills/git-push-to-server/commit_and_push.sh --message-file <path> [--branch <b>] [--remote origin] [--no-push]"
            echo "  --message-file <path>  message 文本文件（git commit -F 读取）"
            echo "  --branch <b>           推送分支（默认当前分支）"
            echo "  --remote <name>        远程名（默认 origin）"
            echo "  --no-push              只 commit 不 push"
            exit 0 ;;
        *) log_error "未知参数: $1"; exit 1 ;;
    esac
done

# ============================================================================
# 校验
# ============================================================================
cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; exit 1; }

if [ -z "$MESSAGE_FILE" ]; then
    log_error "缺少必填参数 --message-file"
    exit 1
fi
if [ ! -f "$MESSAGE_FILE" ]; then
    log_error "message 文件不存在: $MESSAGE_FILE"
    exit 1
fi
if [ ! -s "$MESSAGE_FILE" ]; then
    log_error "message 文件为空: $MESSAGE_FILE"
    exit 1
fi

# 分支默认值：当前分支
if [ -z "$BRANCH" ]; then
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
        log_error "无法确定当前分支，请用 --branch 显式指定"
        exit 1
    fi
fi

# ============================================================================
# Step 1: git add -A
# ============================================================================
log_step "Step 1: 暂存所有改动"
git add -A || { log_error "git add -A 失败"; exit 1; }

STAGED_COUNT=$(git diff --cached --name-only 2>/dev/null | grep -c '.' || true)
if [ "$STAGED_COUNT" -eq 0 ]; then
    log_error "无改动可提交（git add -A 后暂存区为空）"
    exit 1
fi
log_info "已暂存 $STAGED_COUNT 个文件"

# ============================================================================
# Step 2: git commit -F
# ============================================================================
log_step "Step 2: 提交"
if ! git commit -F "$MESSAGE_FILE"; then
    log_error "git commit 失败"
    exit 1
fi
COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
log_info "提交成功: $COMMIT_HASH"

# ============================================================================
# Step 3: git push（可选）
# ============================================================================
if [ "$NO_PUSH" = true ]; then
    log_info "--no-push 模式，跳过推送"
    echo ""
    log_step "完成（未推送）"
    echo "  commit: $COMMIT_HASH"
    echo "  分支:   $BRANCH (仅本地)"
    exit 0
fi

log_step "Step 3: 推送"
log_info "目标: $REMOTE/$BRANCH"
# 注意：push 失败不回退 commit，保留现场让用户手动处理
if ! git push "$REMOTE" "$BRANCH"; then
    echo ""
    log_error "git push 失败"
    log_error "commit 已保留（本地 $COMMIT_HASH），未自动回退"
    log_error "请手动处理，例如:"
    log_error "  git push $REMOTE $BRANCH                      # 重试"
    log_error "  git pull --rebase $REMOTE $BRANCH && git push # 拉取并 rebase 后重推"
    log_error "  git reset --soft HEAD~1                       # 回退 commit（改动回到暂存区）"
    exit 2
fi
REMOTE_URL=$(git remote get-url "$REMOTE" 2>/dev/null || echo "unknown")
echo ""
log_step "完成"
echo "  commit: $COMMIT_HASH"
echo "  推送:   $REMOTE/$BRANCH ($REMOTE_URL)"

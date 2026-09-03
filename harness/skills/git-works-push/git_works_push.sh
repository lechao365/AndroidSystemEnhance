#!/usr/bin/env bash
# git-works-push（项目定制精简版）：collect diff → commit → push origin dev。
# 保留：永不推 main 守卫、--push-only、--dry-run、push 失败 commit 保留(exit 2)。
# 去掉：dev 自动创建、amend、message 三重校验。
set -euo pipefail
BRANCH="${GIT_WORKS_BRANCH:-dev}"
MODE="normal"   # normal | push-only | dry-run
MSG_FILE=""
BASELINE_STATUS=""
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  echo "usage: $0 [--push-only] [--dry-run] [--message-file <f>] [--baseline-status <f>]"
  exit 3
}

while [ $# -gt 0 ]; do
  case "$1" in
    --push-only) MODE="push-only" ;;
    --dry-run) MODE="dry-run" ;;
    --message-file) [ $# -ge 2 ] || usage; MSG_FILE="$2"; shift ;;
    --baseline-status) [ $# -ge 2 ] || usage; BASELINE_STATUS="$2"; shift ;;
    *) usage ;;
  esac
  shift
done

# 运行日志：harness/log/git-works-push/<时间戳>.log（/harness/log/ 已 gitignore，不入库）
LOG_DIR="$SCRIPT_DIR/../../log/git-works-push"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S).log"
out() { echo "$@" | tee -a "$LOG_FILE"; }
err() { echo "$@" | tee -a "$LOG_FILE" >&2; }

# 永不推 main 守卫
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  err "error: 禁止推送到 $BRANCH"; exit 1
fi
CUR=$(git branch --show-current)
if [ -z "$CUR" ] || [ "$CUR" != "$BRANCH" ]; then
  err "error: 当前分支 $CUR 非 $BRANCH（含 detached HEAD），禁止提交"; exit 1
fi

if [ "$MODE" = "dry-run" ]; then
  out "== dry-run：改动预览（不执行 add/commit/push）=="
  out "== 工作树状态（status --porcelain）=="
  git status --porcelain | tee -a "$LOG_FILE"
  out "== diff --stat（完整）=="
  git diff HEAD --stat | tee -a "$LOG_FILE"
  out "== 未跟踪/新增文件（untracked）=="
  git ls-files --others --exclude-standard | tee -a "$LOG_FILE"
  exit 0
fi

if [ "$MODE" = "normal" ]; then
  [ -n "$MSG_FILE" ] && [ -f "$MSG_FILE" ] || { err "error: 需 --message-file 且文件存在"; exit 3; }
  [ -n "$(git status --porcelain)" ] || { err "working tree clean"; exit 4; }
  # 提交信息中文前缀校验（commit-message-format.md）：首行须为
  # <中文type>(<scope>): <subject>，中文 type 词表限定；英文前缀（feat/fix 等）
  # 一律拒绝，防提交风格漂移（曾出现 feat(harness) 英文前缀混入）
  SUBJECT=$(head -1 "$MSG_FILE")
  if ! printf '%s' "$SUBJECT" | grep -qE '^(新增|修复|重构|文档|构建|杂项)\([^)]*\): '; then
    err "error: 提交信息首行须为 <中文type>(<scope>): <subject>（type 词表：新增/修复/重构/文档/构建/杂项），英文前缀拒绝。实际: $SUBJECT"
    exit 1
  fi
  # 基线声明护栏：提交标题声明 BL-xxx 时须已在登记表登记（防未登记基线混入；
  # 曾提交标题声明 BL-20260828-02 而登记表无此条目，提交后 promote 证据链断裂）
  # 仅对 subject 首行提取声明（commit-message-format.md 约定基线声明位于标题），
  # 避免正文历史/示例引用误伤；测试可经 --baseline-status 注入 mock 登记表。
  [ -n "$BASELINE_STATUS" ] || BASELINE_STATUS="$SCRIPT_DIR/../../config/baseline-status.yaml"
  if [ ! -f "$BASELINE_STATUS" ]; then
    err "error: 基线登记表缺失 $BASELINE_STATUS，无法校验基线声明"; exit 1
  fi
  DECLARED=$(head -1 "$MSG_FILE" | grep -oE 'BL-[0-9]{8}-[0-9]{2}' || true)
  if [ -n "$DECLARED" ]; then
    REGISTERED=$(grep -oE 'baseline_id: (BL-[0-9]{8}-[0-9]{2})' "$BASELINE_STATUS" \
                 | awk '{print $2}' | tr '\n' ' ')
    for BL in $DECLARED; do
      case " $REGISTERED " in
        *" $BL "*) : ;;
        *)
          err "error: 提交声明基线 $BL 未在登记表登记，拒绝提交（基线须先经 /publish-main-base --prepare 登记）"
          exit 1
          ;;
      esac
    done
  fi
  git add -A || { err "error: git add 失败"; exit 1; }
  git commit -F "$MSG_FILE" || { err "error: commit 失败"; exit 1; }
fi

# push 失败分类：non-fast-forward（远端领先）给出可恢复提示，其余给原始输出
if ! PUSH_OUTPUT=$(git push -u origin "$BRANCH" 2>&1); then
  case "$PUSH_OUTPUT" in
    *"non-fast-forward"*|*"fetch first"*|*"[rejected]"*)
      err "error: push 被拒（远端 $BRANCH 领先，non-fast-forward）。请 git pull --rebase origin $BRANCH 后重试，或确认本地后 --push-only"
      ;;
    *)
      err "error: push 失败（commit 已保留）。输出："
      err "$PUSH_OUTPUT"
      ;;
  esac
  exit 2
fi

# 推送后核对远端 sha；慢网络/服务端 hook 未完成时 ls-remote 可能短暂滞后，重试 3 次
LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=""
for _ in 1 2 3; do
  REMOTE_SHA=$(git ls-remote origin "refs/heads/$BRANCH" | awk '{print $1}') || true
  [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" = "$LOCAL_SHA" ] && break
  sleep 1
done
if [ -z "$REMOTE_SHA" ]; then
  # ls-remote 对不存在的 ref 返回空输出但 exit 0，须显式拦截，否则落到下方误导文案
  err "error: 远端 $BRANCH 引用无输出（refs/heads/$BRANCH 不存在或 ls-remote 异常）"; exit 2
fi
if [ "$REMOTE_SHA" != "$LOCAL_SHA" ]; then
  err "error: 远端 $BRANCH（$REMOTE_SHA）与本地 HEAD（$LOCAL_SHA）不符，疑似推送未生效"; exit 2
fi
out "pushed: $BRANCH $(git rev-parse --short HEAD)"
exit 0

#!/usr/bin/env bash
# git-works-push（项目定制精简版）：collect diff → commit → push origin dev。
# 保留：永不推 main 守卫、--push-only、--dry-run、push 失败 commit 保留(exit 2)。
# 去掉：dev 自动创建、amend、message 三重校验。
set -euo pipefail
BRANCH="${GIT_WORKS_BRANCH:-dev}"
MODE="normal"   # normal | push-only | dry-run
MSG_FILE=""

usage() { echo "usage: $0 [--push-only] [--dry-run] [--message-file <f>]"; exit 3; }

while [ $# -gt 0 ]; do
  case "$1" in
    --push-only) MODE="push-only" ;;
    --dry-run) MODE="dry-run" ;;
    --message-file) [ $# -ge 2 ] || usage; MSG_FILE="$2"; shift ;;
    *) usage ;;
  esac
  shift
done

# 永不推 main 守卫
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  echo "error: 禁止推送到 $BRANCH" >&2; exit 1
fi
CUR=$(git branch --show-current)
if [ -z "$CUR" ] || [ "$CUR" != "$BRANCH" ]; then
  echo "error: 当前分支 $CUR 非 $BRANCH（含 detached HEAD），禁止提交" >&2; exit 1
fi

if [ "$MODE" = "dry-run" ]; then
  echo "== dry-run：改动预览（不执行 add/commit/push）=="
  git status --porcelain
  git diff HEAD --stat | tail -5
  exit 0
fi

if [ "$MODE" = "normal" ]; then
  [ -n "$MSG_FILE" ] && [ -f "$MSG_FILE" ] || { echo "error: 需 --message-file 且文件存在" >&2; exit 3; }
  [ -n "$(git status --porcelain)" ] || { echo "working tree clean" >&2; exit 4; }
  git add -A || { echo "error: git add 失败" >&2; exit 1; }
  git commit -F "$MSG_FILE" || { echo "error: commit 失败" >&2; exit 1; }
fi

if ! git push -u origin "$BRANCH"; then
  echo "error: push 失败（commit 已保留），请人工处理（如 pull --rebase 后 --push-only）" >&2
  exit 2
fi
REMOTE_SHA=$(git ls-remote origin "refs/heads/$BRANCH" | awk '{print $1}') || {
  echo "error: 无法获取远端 $BRANCH 引用（refs/heads/$BRANCH）" >&2; exit 2; }
if [ -z "$REMOTE_SHA" ]; then
  # ls-remote 对不存在的 ref 返回空输出但 exit 0，须显式拦截，否则落到下方误导文案
  echo "error: 远端 $BRANCH 引用无输出（refs/heads/$BRANCH 不存在或 ls-remote 异常）" >&2
  exit 2
fi
LOCAL_SHA=$(git rev-parse HEAD)
if [ "$REMOTE_SHA" != "$LOCAL_SHA" ]; then
  echo "error: 远端 $BRANCH（$REMOTE_SHA）与本地 HEAD（$LOCAL_SHA）不符，疑似推送未生效" >&2
  exit 2
fi
echo "pushed: $BRANCH $(git rev-parse --short HEAD)"
exit 0
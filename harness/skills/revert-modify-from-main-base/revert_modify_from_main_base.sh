#!/usr/bin/env bash
# revert-modify-from-main-base：dev 硬重置 origin/main + force push + revert 收据。
# 仅人工触发；无 --execute 时仅预览丢弃清单（确认门），不做任何变更。
# 执行须 --execute --confirm <dev前12位>（token 不匹配即拒）。
set -euo pipefail

usage() { echo "usage: $0 [--execute --confirm <dev前12位>]"; exit 3; }

EXECUTE=0; CONFIRM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --execute) EXECUTE=1 ;;
    --confirm) [ $# -ge 2 ] || usage; CONFIRM="$2"; shift ;;
    *) usage ;;
  esac
  shift
done

# fetch 始终先做，确保 origin/main 为最新（预览清单与实际丢弃一致）
git fetch origin || exit 1

if [ "$EXECUTE" -ne 1 ]; then
  echo "== 将丢弃 origin/main..dev 的提交 =="
  git log origin/main..dev --oneline
  echo "确认后执行: $0 --execute --confirm $(git rev-parse --short=12 dev)"
  exit 0
fi

# 确认 token：必须等于当前 dev 前 12 位，防误触发
HEAD12=$(git rev-parse --short=12 dev)
[ "$CONFIRM" = "$HEAD12" ] || {
  echo "error: --confirm（$CONFIRM）!= 当前 dev 前12位（$HEAD12），拒绝执行" >&2; exit 1; }
# 工作树预检：未提交改动会被 reset --hard 静默销毁
[ -z "$(git status --porcelain)" ] || {
  echo "error: 工作树非空（未提交改动将被销毁），请先提交或 stash" >&2; exit 1; }

OLD=$(git rev-parse dev)
CNT=$(git rev-list --count origin/main.."$OLD")
git checkout dev || exit 1
git reset --hard origin/main || exit 1
git push --force origin dev || { echo "error: force push 失败" >&2; exit 2; }

# revert 收据统一走 ws_report.py（模式 B）：自动落盘 + trend.md 行，消除双份格式来源
BODYF=$(mktemp)
{
  echo "## 被丢弃提交"
  git log "$OLD" --not origin/main --oneline
} > "$BODYF"
WS_OUT=$(python3 harness/skills/workspace-verify/ws_report.py \
  --target "$(git rev-parse --short=12 origin/main)" \
  --prefix revert --result revert \
  --summary "回退 dev 到 main（丢弃 ${CNT} 个提交，起点 ${OLD:0:12}）" \
  --body "$BODYF") || { rm -f "$BODYF"; echo "error: revert 收据写入失败" >&2; exit 1; }
rm -f "$BODYF"
RCPT=$(echo "$WS_OUT" | sed -n 's/^receipt: //p')
[ -n "$RCPT" ] || { echo "error: ws_report 未输出收据路径" >&2; exit 1; }
# 收据随 dev 提交推送（trend.md 由 ws_report append_trend 写入，须一并入提交，否则脏树使下次预检必败）
git add "$RCPT" data/verify-results/trend.md
if git diff --cached --quiet; then
  echo "warn: 无收据变更，跳过提交"
else
  git commit -m "杂项(dev): 回退 dev 至 main 基线（丢弃 ${CNT} 提交）" || {
    echo "error: revert 收据提交失败" >&2; exit 1; }
fi
git push origin dev || { echo "error: revert 收据推送失败，请人工 push" >&2; exit 2; }

echo "revert 收据: $RCPT"
echo "AI 须立即执行恢复验证：/workspace-verify（模式 B：--target main --prefix revert，默认含 boot 验收）"
exit 0
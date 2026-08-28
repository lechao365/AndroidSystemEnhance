#!/usr/bin/env bash
# sync-modify-to-main-base：dev → main squash promote + 重建 dev + baseline 晋升。
# 前置：最新收据 result∈{pass,skip} 且 HEAD^(--short=12) == verified_commit。
# prepare：登记 candidate（随 dev 提交推送）；promote：晋升 promoted + squash + 重建。
set -euo pipefail
MODE=""; MSG_FILE=""; BID=""

usage() { echo "usage: $0 --prepare | --promote --baseline-id <id> --message-file <f> | --check-only"; exit 3; }
[ $# -ge 1 ] || usage
case "$1" in
  --prepare) MODE="prepare" ;;
  --check-only) MODE="check-only" ;;
  --promote)
    MODE="promote"; shift
    while [ $# -gt 0 ]; do
      case "$1" in
        --baseline-id) [ $# -ge 2 ] || usage; BID="$2"; shift ;;
        --message-file) [ $# -ge 2 ] || usage; MSG_FILE="$2"; shift ;;
        *) usage ;;
      esac
      shift
    done ;;
  *) usage ;;
esac

# ── 工作树预检（prepare/promote；check-only 干跑不做 add/commit/squash，脏树无害）───
# 未提交改动会被后续 git add/commit/squash 静默吞并，先拒绝
if [ "$MODE" != "check-only" ]; then
  [ -z "$(git status --porcelain)" ] || {
    echo "error: 工作树非空（未提交改动将干扰 promote/登记提交），请先提交或 stash" >&2; exit 1; }
fi

# ── 前置校验（prepare/promote/check-only 共用；sha 统一 short=12 比较）─────────
RECEIPT_INFO=$(python3 - <<'PYEOF'
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path("harness/skills/cross-device/lib/python").resolve()))
import cdp_receipt
path, r = cdp_receipt.latest_receipt_with_path()
if r is None:
    sys.exit(1)
# 头注释约定相对项目根输出（脚本从项目根运行，relpath 相对 cwd），避免泄露 home 绝对路径
print(os.path.relpath(path))
print(r.result)
print(r.verified_commit)
PYEOF
) || true
[ -n "$RECEIPT_INFO" ] || { echo "error: 无 verify 收据" >&2; exit 1; }
LATEST=$(echo "$RECEIPT_INFO" | sed -n '1p')
RESULT=$(echo "$RECEIPT_INFO" | sed -n '2p')
VC=$(echo "$RECEIPT_INFO" | sed -n '3p')
case "$RESULT" in
  pass|skip) ;;
  *) echo "error: 最新收据 result=$RESULT 非 pass/skip（revert/fail 收据不可 promote）" >&2; exit 1 ;;
esac
# 跳过「构建(baseline):」元提交定位内容提交 BH，PARENT 取 BH 父提交
# （prepare 登记 candidate 的提交会使 HEAD^ 不再是 verified_commit，promote 必失败）
BH=$(git rev-parse HEAD)
while [[ "$(git log -1 --format=%s "$BH")" == "构建(baseline):"* ]]; do
  BH=$(git rev-parse "$BH^" 2>/dev/null) || {
    echo "error: BH 回溯越界（$BH 无父提交）" >&2; exit 1; }
done
PARENT=$(git rev-parse --short=12 "$BH^" 2>/dev/null || echo "")
[ "$PARENT" = "$VC" ] || {
  echo "error: HEAD^($PARENT) != verified_commit($VC)：dev 存在未验证改动" >&2; exit 1; }

if [ "$MODE" = "check-only" ]; then
  echo "前置校验通过：PARENT=$PARENT verified_commit=$VC result=$RESULT"
  exit 0
fi

if [ "$MODE" = "prepare" ]; then
  git fetch origin || { echo "error: fetch 失败" >&2; exit 1; }
  CNT=$(git rev-list --count main..dev)
  [ "$CNT" -gt 0 ] || { echo "dev 无领先 main 的提交（exit 4）"; exit 4; }
  python3 harness/skills/sync-modify-to-main-base/baseline_register.py add-candidate \
    --source-commit "$(git rev-parse --short=12 HEAD)" --receipt-path "$LATEST" \
    || { echo "error: candidate 登记失败" >&2; exit 1; }
  # 登记随 dev 提交推送（避免弄脏工作树阻塞后续 precheck）
  git add harness/config/baseline-status.yaml
  if git diff --cached --quiet; then
    echo "warn: baseline-status.yaml 无变更，跳过登记提交"
  else
    git commit -m "构建(baseline): 登记 candidate（receipt=$(basename "$LATEST")）" || {
      echo "error: candidate 登记提交失败" >&2; exit 1; }
  fi
  git push origin dev || { echo "error: candidate 登记推送失败，请人工 push" >&2; exit 2; }
  echo "candidate 已登记并推送；人工评审后执行："
  echo "  $0 --promote --baseline-id <id> --message-file <f>"
  exit 0
fi

# ── promote ────────────────────────────────────────────────────────
# checkout main 至 push main 间任一步失败的回滚：回 dev、丢弃晋升元提交，
# baseline 改回 candidate（不能后移，squash 需它在 dev 上）
rollback_promote() {
  # 先清掉 main 上的 squash 暂存（否则 checkout dev 失败），再回 dev 丢弃晋升元提交
  git reset --hard HEAD || exit 1
  git checkout dev || exit 1
  git reset --hard HEAD^ || exit 1
  python3 harness/skills/sync-modify-to-main-base/baseline_register.py revert-candidate \
    --baseline-id "$BID" || echo "warn: baseline ${BID} 回退 candidate 失败，请人工处理"
}
[ -n "$BID" ] || { echo "error: --baseline-id 必填" >&2; exit 3; }
[ -n "$MSG_FILE" ] && [ -f "$MSG_FILE" ] || { echo "error: --message-file 缺失或不存在" >&2; exit 3; }
python3 harness/skills/sync-modify-to-main-base/baseline_register.py promote \
  --baseline-id "$BID" \
  || { echo "error: baseline 晋升登记失败（检查 $BID 是否为 candidate）" >&2; exit 1; }
# 晋升登记随 dev 提交（squash 时一并进入 main；重建 dev 后仍在——reset --hard 前）
git add harness/config/baseline-status.yaml
if git diff --cached --quiet; then
  echo "warn: baseline-status.yaml 无变更，跳过晋升提交"
else
  git commit -m "构建(baseline): ${BID} 晋升 promoted" || {
    echo "error: 晋升登记提交失败" >&2; exit 1; }
fi

git checkout main && git pull origin main || { rollback_promote; echo "error: checkout/pull main 失败" >&2; exit 1; }
git merge --squash dev || { rollback_promote; echo "error: merge --squash 失败" >&2; exit 1; }
git commit -F "$MSG_FILE" || { rollback_promote; echo "error: squash commit 失败" >&2; exit 1; }
git diff --quiet main dev || { rollback_promote; echo "error: squash 后 main 与 dev 内容不一致" >&2; exit 1; }
git push origin main || { rollback_promote; echo "error: push main 失败（dev 已含 baseline 晋升提交）" >&2; exit 2; }

# 重建 dev（delete 失败则强推 +dev；再失败转人工）
git checkout dev && git reset --hard main || {
  echo "error: dev 重建失败。恢复指引：git checkout dev && git reset --hard main，然后重新执行本脚本（promote 登记已在 dev 上）" >&2; exit 2; }
if git push origin --delete dev 2>/dev/null; then
  git push -u origin dev || { echo "error: dev 重建推送失败" >&2; exit 2; }
else
  git push origin +dev || { echo "error: dev 强推失败，请人工处理" >&2; exit 2; }
fi

echo "promote 完成；AI 须立即执行 /sync-code-to-doc 工作流同步设计文档"
exit 0
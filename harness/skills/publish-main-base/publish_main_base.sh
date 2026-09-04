#!/usr/bin/env bash
# publish-main-base：基线发布编排器末两步（candidate 登记 + dev → main squash promote）。
# 前置：最新收据 result∈{pass,skip} 且 最近内容提交的父(short=12) == verified_commit；
#       known-issues 门禁无条件执行（实现下移 baseline_register.py check-issues）：
#       先判登记畸形（validate_issue 有红即拒），--task 缺省时从 status 非 fixed 条目
#       的 task 集合自动推断，再判目标任务下存在 origin=introduced 或 blocking 且
#       status!=fixed 的问题即拒。门禁不再依赖显式 task。
# --check（= --check-only）：干跑前置校验，失败时输出 check_class=<分类> 供编排分流：
#   NEED_VERIFY（存在未验证改动→进验证路径）/ NO_RECEIPT（无收据）/ RECEIPT_FAIL（收据
#   result 非 pass/skip）/ DOC_VIOLATION（文档提交夹带非 docs/ 或 prepare 前已存在文档提交）/
# KI_BLOCKED（known-issues 门禁）。prepare/promote 模式分类行仅提示不阻断既有行为。
# promote 收紧：最新收据须 result=pass 且 verify_mode=board；dev 相对 origin/main
# 无 code/ 改动时豁免放行并 warn，否则 RECEIPT_FAIL 拒绝。
# KIGATE：门禁结论四值——pass（显式 --task 通过）/ inferred（缺省推断唯一 task 通过）/
# empty-registry（无活跃任务放行）/ not-run（门禁未执行兜底），随 add-candidate
# --ki-gate 写入 candidate evidence（known-issues 证据链）。
# verified tag：promote 对 BH 打注解 tag verified/<id> 并推送（同名即拒退 3）；
# squash 后 push main 前以 baseline_register.py verify-tree 断言 tag 与 main 树等价
# （排除登记 yaml 与 docs），失败回滚（rollback 一并删本地/远端 tag）。
# prepare：登记 candidate（随 dev 提交推送）；promote：晋升 promoted + squash + 重建。
#
# 提交分类约定（前置校验据此回溯定位最近内容提交）：
#   - 登记元提交：subject 以「构建(baseline):」开头（prepare/promote 自动产生，免验证）
#   - 文档提交：subject 以「文档(」开头，且仅改动 docs/**（promote 前 /sync-code-to-doc 产生；
#     须通过 docs/** 路径校验，防「文档(」前缀夹带未验证代码随 squash 混入 main）
#   - 内容提交：其余（须经验证；最近内容提交的父须等于 verified_commit）
set -euo pipefail
MODE=""; MSG_FILE=""; BID=""; TASK=""; APPROVED_BY=""; EVIDENCE_SCOPE=""
# check 模式失败分类输出（stderr；prepare/promote 亦输出，不影响既有行为与退出码）
check_class() { echo "check_class=$1" >&2; }

usage() { echo "usage: $0 --check [--task <id>] | --prepare [--task <id>] [--evidence-scope <scope>] | --promote --baseline-id <id> --message-file <f> [--task <id>] [--approved-by <id>] [--evidence-scope <scope>] | --rollback --baseline-id <id>"; exit 3; }
[ $# -ge 1 ] || usage
case "$1" in
  --prepare) MODE="prepare"; shift ;;
  --check) MODE="check-only"; shift ;;
  --check-only) MODE="check-only"; shift ;;
  --promote)
    MODE="promote"; shift
    while [ $# -gt 0 ]; do
      case "$1" in
        --baseline-id) [ $# -ge 2 ] || usage; BID="$2"; shift ;;
        --message-file) [ $# -ge 2 ] || usage; MSG_FILE="$2"; shift ;;
        --task) [ $# -ge 2 ] || usage; TASK="$2"; shift ;;
        --approved-by) [ $# -ge 2 ] || usage; APPROVED_BY="$2"; shift ;;
        --evidence-scope) [ $# -ge 2 ] || usage; EVIDENCE_SCOPE="$2"; shift ;;
        *) usage ;;
      esac
      shift
    done ;;
  --rollback)
    # 人工回滚入口：与 promote 失败点共用 rollback_promote（状态推导，见函数头注）
    MODE="rollback"; shift
    while [ $# -gt 0 ]; do
      case "$1" in
        --baseline-id) [ $# -ge 2 ] || usage; BID="$2"; shift ;;
        *) usage ;;
      esac
      shift
    done
    [ -n "$BID" ] || usage ;;
  *) usage ;;
esac

# ── 晋升回滚（promote 失败点与人工 --rollback 共用同一实现）──────────────────
# 状态文件（harness/log/cross-device/promote-<id>.head）记录 promote 进入时的 dev
# HEAD 作为回滚基准；rollback 据此做状态推导，不再盲目回退：
#   1) 登记已落（工作区 yaml 该条目 status=promoted）→ revert-candidate（尽力而为）
#   2) 基准存在且 HEAD 恰前进一位（HEAD^ == 基准，即确有晋升元提交）→ 才 reset 丢弃；
#      更早阶段失败时 HEAD 未前进，盲 reset HEAD^ 会误删合法提交，故跳过并提示人工核查
promote_state_file() { echo "harness/log/cross-device/promote-$1.head"; }

rollback_promote() {
  git fetch origin -q || true
  if [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ]; then
    git reset --hard origin/main || exit 1
  fi
  git checkout dev || exit 1
  # verified tag 一并回滚（本地 + 远端尽力而为），防残留假锚点阻断后续重试
  git tag -d "verified/$BID" >/dev/null 2>&1 || true
  git push origin ":refs/tags/verified/$BID" >/dev/null 2>&1 || true
  # 推导一：登记已执行（工作区该条目已 promoted）才回退 candidate；
  # revert-candidate 仅改工作区 yaml 不产生提交，不影响下方 HEAD 推导
  if python3 - "$BID" <<'PYEOF'
import sys, yaml
try:
    rows = (yaml.safe_load(open("harness/config/baseline-status.yaml", encoding="utf-8"))
            or {}).get("baselines") or []
except FileNotFoundError:
    sys.exit(1)
bid = sys.argv[1]
sys.exit(0 if any(b.get("baseline_id") == bid and b.get("status") == "promoted"
                  for b in rows) else 1)
PYEOF
  then
    python3 harness/skills/publish-main-base/baseline_register.py revert-candidate \
      --baseline-id "$BID" || echo "warn: baseline ${BID} 回退 candidate 失败，请人工处理"
  fi
  # 推导二：以 promote 前所记 HEAD 为基准，确有新增提交（HEAD^ == 基准）才丢弃
  BASE_HEAD=""
  [ -f "$(promote_state_file "$BID")" ] && BASE_HEAD=$(cat "$(promote_state_file "$BID")")
  if [ -n "$BASE_HEAD" ] \
     && [ "$(git rev-parse HEAD 2>/dev/null)" != "$BASE_HEAD" ] \
     && [ "$(git rev-parse HEAD^ 2>/dev/null || true)" = "$BASE_HEAD" ]; then
    git reset --hard "$BASE_HEAD" || exit 1
  else
    echo "info: 未检出晋升元提交（基准缺失或 HEAD 未按预期前进），跳过 dev reset，请人工核查"
  fi
  rm -f "$(promote_state_file "$BID")"
}

# 人工回滚直通：跳过工作树预检/收据校验（回滚现场允许脏树），直进直出
if [ "$MODE" = "rollback" ]; then
  rollback_promote
  exit 0
fi
# 通用前置参数（prepare/check-only/promote 亦可指定）
while [ $# -gt 0 ]; do
  case "$1" in
    --task) [ $# -ge 2 ] || usage; TASK="$2"; shift ;;
    --evidence-scope) [ $# -ge 2 ] || usage; EVIDENCE_SCOPE="$2"; shift ;;
    *) usage ;;
  esac
  shift
done

# ── 工作树预检（prepare/promote；check-only 干跑不做 add/commit/squash，脏树无害）───
# 未提交改动会被后续 git add/commit/squash 静默吞并，先拒绝
if [ "$MODE" != "check-only" ]; then
  [ -z "$(git status --porcelain)" ] || {
    echo "error: 工作树非空（未提交改动将干扰 promote/登记提交），请先提交或 stash" >&2; exit 1; }
fi

# ── 项目根改道防线（方向 4）：CDP_PROJECT_ROOT 若已设且不等于 git 顶层目录即拒绝
# （收据查找依赖 CDP_PROJECT_ROOT 定位 log_apply_dir，防环境变量把收据目录改道）
if [ -n "${CDP_PROJECT_ROOT:-}" ]; then
  TOP="$(git rev-parse --show-toplevel)"
  if [ "$CDP_PROJECT_ROOT" != "$TOP" ]; then
    echo "error: CDP_PROJECT_ROOT=$CDP_PROJECT_ROOT 不等于 git 顶层目录 $TOP，拒绝执行（防收据目录改道）" >&2
    exit 1
  fi
fi

# ── 前置校验（prepare/promote/check-only 共用；sha 统一 short=12 比较）─────────
RECEIPT_INFO=$(python3 - <<'PYEOF'
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path("harness/skills/cross-device/lib/python").resolve()))
import cdp_receipt
path, r, receipt_errs = cdp_receipt.latest_receipt_with_path()
if r is None:
    sys.exit(1)
# 方向 5：收据解析有错（非法整数/重复字段/schema 非 1）即拒，不再静默吞错
if receipt_errs:
    print("error: 最新收据解析错误: " + "; ".join(receipt_errs), file=sys.stderr)
    sys.exit(1)
# 头注释约定相对项目根输出（脚本从项目根运行，relpath 相对 cwd），避免泄露 home 绝对路径
print(os.path.relpath(path))
print(r.result)
print(r.verified_commit)
print(r.verify_mode)
print(r.verified_tree)
PYEOF
) || true
[ -n "$RECEIPT_INFO" ] || { check_class NO_RECEIPT; echo "error: 无 verify 收据" >&2; exit 1; }
LATEST=$(echo "$RECEIPT_INFO" | sed -n '1p')
RESULT=$(echo "$RECEIPT_INFO" | sed -n '2p')
VC=$(echo "$RECEIPT_INFO" | sed -n '3p')
MODEV=$(echo "$RECEIPT_INFO" | sed -n '4p')
VTREE=$(echo "$RECEIPT_INFO" | sed -n '5p')
# 输出完整性校验：四行齐备，缺行说明收据查询输出异常（错误被 || true 吞没时兜底）
[ -n "$LATEST" ] && [ -n "$RESULT" ] && [ -n "$VC" ] && [ -n "$MODEV" ] || {
  check_class NO_RECEIPT
  echo "error: 收据解析输出不完整（期望 4 行，实际 $(echo "$RECEIPT_INFO" | wc -l) 行）" >&2; exit 1; }
case "$RESULT" in
  pass|skip) ;;
  *) check_class RECEIPT_FAIL; echo "error: 最新收据 result=$RESULT 非 pass/skip（revert/fail 收据不可 promote）" >&2; exit 1 ;;
esac

# ── 回溯定位最近内容提交 BH（跳过登记元提交与文档提交），PARENT 取 BH 父提交 ──────
# （prepare 登记 candidate 的提交会使 HEAD^ 不再是 verified_commit；文档同步提交同理，
#   故回溯须一并跳过；promote 必失败于 PARENT 校验）
classify() {
  case "$1" in
    "构建(baseline):"*) echo "meta" ;;
    "文档("*) echo "doc" ;;
    *) echo "content" ;;
  esac
}
BH=$(git rev-parse HEAD)
SKIP_META=0; SKIP_DOC=0; DOC_SHA_LIST=""
while :; do
  MSG=$(git log -1 --format=%s "$BH")
  case "$(classify "$MSG")" in
    meta)
      SKIP_META=$((SKIP_META+1))
      if ! BH=$(git rev-parse "$BH^"); then echo "error: BH 回溯越界（$BH 无父提交）" >&2; exit 1; fi
      ;;
    doc)
      SKIP_DOC=$((SKIP_DOC+1)); DOC_SHA_LIST="$DOC_SHA_LIST $BH"
      if ! BH=$(git rev-parse "$BH^"); then echo "error: BH 回溯越界（$BH 无父提交）" >&2; exit 1; fi
      ;;
    *) break ;;
  esac
done
# 文档提交须仅改动 docs/**（防「文档(」前缀夹带未验证代码随 squash 混入 main）
for D in $DOC_SHA_LIST; do
  BAD=$(git show --name-only --format= "$D" | grep -v '^docs/' | grep -v '^$' || true)
  if [ -n "$BAD" ]; then
    check_class DOC_VIOLATION
    echo "error: 文档提交 $D 含非 docs/ 改动（$(echo "$BAD" | tr '\n' ' ')），拒绝（防未验证代码夹带）" >&2
    exit 1
  fi
done
# prepare 严格模式：prepare 阶段不应存在文档提交（文档同步在 prepare 之后、promote 之前）
if [ "$MODE" = "prepare" ] && [ "$SKIP_DOC" -gt 0 ]; then
  check_class DOC_VIOLATION
  echo "error: prepare 前 dev 已存在 $SKIP_DOC 个文档提交（文档同步应在 prepare 登记之后、promote 前执行），拒绝" >&2
  exit 1
fi
PARENT=$(git rev-parse --short=12 "$BH^" 2>/dev/null || echo "")
[ "$PARENT" = "$VC" ] || {
  check_class NEED_VERIFY
  echo "error: 最近内容提交父($PARENT) != verified_commit($VC)：dev 存在未验证改动（跳过 meta=$SKIP_META doc=$SKIP_DOC）" >&2; exit 1; }

# ── known-issues 门禁（prepare/promote/check-only 共用，无条件执行）────────
# 门禁实现下移 baseline_register.py check-issues：先判畸形登记（validate_issue
# 有红即拒），再判目标任务 origin=introduced/blocking 且 status!=fixed 即拒。
# --task 缺省时由 check-issues 自动推断：status 非 fixed 条目 task 集合唯一→采用
# （KIGATE=inferred）、多值→拒、空集→empty-registry 放行（无活跃任务无冲突对象）。
# KIGATE 记门禁结论（pass/inferred/empty-registry/not-run），随 add-candidate
# --ki-gate 写入 candidate 证据链
KIGATE="not-run"
if [ -n "$TASK" ]; then
  if python3 harness/skills/publish-main-base/baseline_register.py check-issues --task "$TASK"; then
    KIGATE="pass"
  else
    check_class KI_BLOCKED
    echo "error: known-issues 门禁未通过（存在未解决阻塞问题或登记畸形）" >&2
    exit 1
  fi
else
  KI_OUT=$(python3 harness/skills/publish-main-base/baseline_register.py check-issues 2>&1) || {
    check_class KI_BLOCKED
    echo "$KI_OUT" >&2
    echo "error: known-issues 门禁未通过（task 推断失败或存在未解决阻塞问题）" >&2
    exit 1
  }
  if printf '%s\n' "$KI_OUT" | grep -q "task=empty-registry"; then
    KIGATE="empty-registry"
  else
    KIGATE="inferred"
    # 提取门禁推断出的唯一活跃任务（输出含 task=<id>），供带病项自动携带匹配
    INFERRED=$(printf '%s\n' "$KI_OUT" | sed -n 's/.*task=\([^ )]*\).*/\1/p' | head -1)
    [ -n "$INFERRED" ] && TASK="$INFERRED"
  fi
  printf '%s\n' "$KI_OUT" >&2
fi

if [ "$MODE" = "check-only" ]; then
  echo "前置校验通过：PARENT=$PARENT verified_commit=$VC result=$RESULT"
  [ "$SKIP_META" -gt 0 ] && echo "  跳过登记元提交: $SKIP_META"
  [ "$SKIP_DOC" -gt 0 ] && echo "  跳过文档提交: $SKIP_DOC（docs/** 路径校验通过）"
  exit 0
fi

if [ "$MODE" = "prepare" ]; then
  # evidence 锚点回溯：add-candidate 的收据取最新 board 收据（SKILL 阶段 3：
  # evidence-scope 缺省从最新 board 收据 cases 推导）——最新收据可能是 -s
  # skip 的 harness 批（cases 为空），直接以它登记会让证据推导无源而死锁；
  # 快路径判定（PARENT==VC）仍以 $LATEST 为准，两者职责分离。
  # 无 board 收据（从未上板验证）时回落 $LATEST，由 add-candidate 从严拒绝
  EVIDENCE_RECEIPT=$(python3 - <<'PYEOF'
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path("harness/skills/cross-device/lib/python").resolve()))
import cdp_receipt
p, _ = cdp_receipt.latest_board_receipt()
print(os.path.relpath(p) if p else "")
PYEOF
  ) || true
  [ -n "$EVIDENCE_RECEIPT" ] || EVIDENCE_RECEIPT="$LATEST"
  # evidence-scope 可选：缺省交 baseline_register add-candidate 从 board 收据 cases
  # 推导（人工传值仅可为收据实测范围子集，防过度声称）
  git fetch origin || { echo "error: fetch 失败" >&2; exit 1; }
  CNT=$(git rev-list --count main..dev)
  [ "$CNT" -gt 0 ] || { echo "dev 无领先 main 的提交（exit 4）"; exit 4; }
  # source_commit 取回溯后的最近内容提交 BH（非 HEAD，避免重复 prepare 时误记登记元提交）
  # 带病项自动携带：从 read_index 取 status 属 open/scheduled 且 task 匹配的条目 id
  #（逗号分隔写入 evidence.known_issues_carried；未显式 --task 时按门禁推断任务匹配，
  #  无匹配任务即记空。只记录不阻断，硬阻断会死锁）
  KNOWN_ISSUES_CARRIED=""
  if [ -n "$TASK" ]; then
    KNOWN_ISSUES_CARRIED=$(TASK_VAL="$TASK" python3 - <<'PYEOF'
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path("harness/skills/cross-device/lib/python").resolve()))
sys.path.insert(0, str(Path("harness/skills/publish-main-base").resolve()))
from baseline_register import carried_issue_ids
print(",".join(carried_issue_ids(os.environ.get("TASK_VAL", ""))))
PYEOF
    ) || true
  fi
  python3 harness/skills/publish-main-base/baseline_register.py add-candidate \
    --source-commit "$(git rev-parse --short=12 "$BH")" --receipt-path "$EVIDENCE_RECEIPT" \
    --ki-gate "$KIGATE" --evidence-scope "$EVIDENCE_SCOPE" \
    --known-issues-carried "$KNOWN_ISSUES_CARRIED" \
    || { echo "error: candidate 登记失败" >&2; exit 1; }
  # 登记随 dev 提交推送（避免弄脏工作树阻塞后续 precheck）
  git add harness/config/baseline-status.yaml
  if git diff --cached --quiet; then
    echo "warn: baseline-status.yaml 无变更，跳过登记提交"
  else
    git commit -m "构建(baseline): 登记 candidate（receipt=$(basename "$EVIDENCE_RECEIPT")）" || {
      echo "error: candidate 登记提交失败" >&2; exit 1; }
  fi
  git push origin dev || { echo "error: candidate 登记推送失败，请人工 push" >&2; exit 2; }
  echo "candidate 已登记并推送；人工评审后执行："
  echo "  $0 --promote --baseline-id <id> --message-file <f> [--task <id>]"
  exit 0
fi

# ── promote ────────────────────────────────────────────────────────
# 记录 promote 进入时的 dev HEAD 作为回滚基准（rollback_promote 状态推导依据）
DEV_HEAD_BEFORE=$(git rev-parse HEAD)
mkdir -p harness/log/cross-device
printf '%s\n' "$DEV_HEAD_BEFORE" > "$(promote_state_file "$BID")"
[ -n "$BID" ] || { echo "error: --baseline-id 必填" >&2; exit 3; }
[ -n "$MSG_FILE" ] && [ -f "$MSG_FILE" ] || { echo "error: --message-file 缺失或不存在" >&2; exit 3; }
# 方向 6：审批凭据外部化——--approved-by 必填（不再回落默认常量，防审批可自证）
[ -n "$APPROVED_BY" ] || { echo "error: --promote 必须传 --approved-by（审批凭据外部化，不再回落默认常量）" >&2; exit 3; }
# promote 不再强制 --task：known-issues 门禁在共用段已无条件执行（缺省推断；
# 推断失败时门禁段 exit 1 拒绝），显式 --task 仅作白名单确认
git fetch origin || { echo "error: fetch 失败" >&2; exit 1; }
# promote 收紧（基线晋升须上板证据）：dev 相对 origin/main 的 code/ 改动须被
# 最新 board 收据覆盖（其 verified_commit 为该 code 改动提交的后代或自身）；
# 最新收据可能是 -s skip 的 harness 批（verify_mode=none），上板证据锚点回溯
# latest_board_receipt，不被 skip 批干扰。dev 无 code/ 改动时豁免放行并 warn，
# 且证据范围改写为 no-code-change（本批无代码改动，原 scope 不适用）；
# 其余（board 收据缺失或未覆盖 code 改动）按 RECEIPT_FAIL 拒绝
PROMOTE_SCOPE="$EVIDENCE_SCOPE"
CODE_HEAD=$(git log --format=%H origin/main...dev -- code/ | head -1)
if [ -z "$CODE_HEAD" ]; then
  echo "warn: 最新收据 result=$RESULT verify_mode=$MODEV 非 pass+board，但 dev 相对 origin/main 无 code/ 改动，豁免放行"
  PROMOTE_SCOPE="no-code-change"
else
  # code/ 改动是否被最新 board 收据覆盖：merge-base --is-ancestor 判覆盖链
  BOARD_INFO=$(CODE_HEAD="$CODE_HEAD" python3 - <<'PYEOF'
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path("harness/skills/cross-device/lib/python").resolve()))
import cdp_receipt
p, r = cdp_receipt.latest_board_receipt()
if not p or not r.verified_commit:
    print("0")
    print("")
    sys.exit(0)
r0 = subprocess.run(["git", "merge-base", "--is-ancestor",
                     os.environ["CODE_HEAD"], r.verified_commit],
                    capture_output=True)
print("1" if r0.returncode == 0 else "0")
print(r.verified_tree)
PYEOF
  ) || true
  BOARD_OK=$(echo "$BOARD_INFO" | sed -n '1p')
  BOARD_VTREE=$(echo "$BOARD_INFO" | sed -n '2p')
  if [ "$BOARD_OK" != "1" ]; then
    check_class RECEIPT_FAIL
    echo "error: promote 要求 code/ 改动被最新 board 收据覆盖（board 收据缺失或 verified_commit 不覆盖 code 改动提交 $CODE_HEAD）" >&2
    exit 1
  fi
  # 发布内容与验证内容绑定（批次 261f10265269 方向 3）：现有树等价断言比
  # promote 时刻所打 tag（自身树恒等），证明不了该树曾被验证；此处比最新
  # board 收据 verified_tree（验证时刻、排除统一集合后的内容树）与晋升内容
  # 树（dev HEAD^{tree} 同一算法同一集合，content_tree.EXCLUDE_PATHS：登记
  # yaml/docs/证据快照/清算目录/收据目录——否则 promote 自身写入会恒判红）
  if [ -z "$BOARD_VTREE" ]; then
    check_class RECEIPT_FAIL
    echo "error: 最新 board 收据缺 verified_tree 字段（旧版 ws_report 产物），无法绑定发布与验证内容；请以当前工具重写收据" >&2
    exit 1
  fi
  PROMOTE_TREE=$(python3 harness/lib/content_tree.py --tree HEAD) || {
    echo "error: 晋升内容树计算失败" >&2; exit 1; }
  if [ "$PROMOTE_TREE" != "$BOARD_VTREE" ]; then
    echo "error: 收据 verified_tree 与晋升内容树不一致（发布内容≠验证内容）：" >&2
    # 树对象不可解析（假树/被 gc）时 diff 失败——pipefail 下须容错，主结论已定
    git diff --name-only "$BOARD_VTREE" "$PROMOTE_TREE" 2>/dev/null | sed 's/^/  /' >&2 \
      || echo "  （树对象不可解析，无法列出差异路径）" >&2
    exit 1
  fi
fi
# 文档同步遗漏提示（warn 不阻断）：dev 相对 origin/main 无 docs/ 改动时提示
if ! git diff --name-only origin/main...dev | grep -q '^docs/'; then
  echo "warn: dev 相对 origin/main 无 docs/ 改动（若本批应同步设计文档，请先 /sync-code-to-doc --base origin/main 并 commit 到 dev）"
fi

# checkout main 至 push main 间任一步失败回滚：调顶层 rollback_promote（与人工
# --rollback 共用同一实现，状态推导见其函数头注——reset 仅在确有晋升元提交时执行）。
# 关键：先清掉 main 上的 squash 暂存/提交（reset 到 origin/main，真正丢弃已 commit 的
# squash，否则 push main 失败后 main 残留未登记 commit 会污染后续任何 push/重试）
# verified tag 锚点：对 BH（最近内容提交）打注解 tag 并推送，供树等价断言与追溯；
# 同名 tag 已存在即拒退 3（重复 promote / baseline_id 复用防线）
if git rev-parse -q --verify "refs/tags/verified/$BID" >/dev/null 2>&1; then
  echo "error: tag verified/$BID 已存在（疑似重复 promote 或 baseline_id 复用），拒绝" >&2
  exit 3
fi
git tag -a "verified/$BID" -m "verified baseline $BID" "$BH" \
  || { echo "error: 打 tag verified/$BID 失败" >&2; exit 1; }
git push origin "refs/tags/verified/$BID" || {
  git tag -d "verified/$BID" >/dev/null 2>&1 || true
  echo "error: 推送 tag verified/$BID 失败" >&2; exit 2; }
python3 harness/skills/publish-main-base/baseline_register.py promote \
  --baseline-id "$BID" --approved-by "$APPROVED_BY" \
  ${PROMOTE_SCOPE:+--evidence-scope "$PROMOTE_SCOPE"} \
  || { echo "error: baseline 晋升登记失败（检查 $BID 是否为 candidate）" >&2; exit 1; }
# 晋升登记随 dev 提交（squash 时一并进入 main；重建 dev 后仍在——reset --hard 前）
# 证据快照目录 data/baselines/ 一并 add（promote 已生成 <id>-<收据名>.md 快照）
# data/known-issues/ 一并 add -A（promote 清算删除的终态条目须随晋升提交入库，
# 否则删除游离在工作树；verify-tree 已排除该目录，清算不破坏树等价断言）
git add harness/config/baseline-status.yaml
if [ -d data/baselines ]; then git add data/baselines; fi
if [ -d data/known-issues ]; then git add -A data/known-issues; fi
if git diff --cached --quiet; then
  echo "warn: baseline-status.yaml 无变更，跳过晋升提交"
else
  git commit -m "构建(baseline): ${BID} 晋升 promoted" || {
    echo "error: 晋升登记提交失败" >&2; exit 1; }
fi

git checkout main && git pull origin main || { rollback_promote; echo "error: checkout/pull main 失败" >&2; exit 1; }
git merge --squash dev || { rollback_promote; echo "error: merge --squash 失败" >&2; exit 1; }
# 一致性检查在 commit 前：暂存区须与 dev tree 一致（此时 main 尚无 commit，rollback 可干净撤销）
git diff --cached --quiet dev || { rollback_promote; echo "error: squash 暂存与 dev 内容不一致" >&2; exit 1; }
git commit -F "$MSG_FILE" || { rollback_promote; echo "error: squash commit 失败" >&2; exit 1; }
# 树等价断言：tag verified/$BID 与 main 树（排除登记 yaml 与 docs）必须无差异，
# 防未验证内容借 meta/doc 提交夹带进 main；失败走 rollback（含删 tag）退 1
python3 harness/skills/publish-main-base/baseline_register.py verify-tree --baseline-id "$BID" \
  || { rollback_promote; echo "error: 树等价断言失败（verified/$BID 与 main 树不一致），已回滚" >&2; exit 1; }
git push origin main || { rollback_promote; echo "error: push main 失败（本地 main 已回退 origin/main，dev 已回退）" >&2; exit 2; }

# 重建 dev（force push 一步覆盖，避免 delete-then-push 的非原子窗口——delete 成功而
# push 失败会导致远程 dev 缺失，协作者引用断裂）
git checkout dev && git reset --hard main || {
  echo "error: dev 重建失败。main 已含基线，请人工完成：git checkout dev && git reset --hard main && git push -f origin dev（勿重跑 promote）" >&2; exit 2; }
git push -f -u origin dev || { echo "error: dev 重建推送失败，请人工处理" >&2; exit 2; }

echo "promote 完成；提示：本批次文档同步应在 promote 前以 /sync-code-to-doc --base origin/main 完成，promote 后工作区已 clean（git diff HEAD 无变动，勿再硬同步）"
exit 0

#!/usr/bin/env bash
# git-works-push（项目定制精简版）：collect diff → commit → push origin dev。
# 保留：永不推 main 守卫、--push-only、--dry-run、push 失败 commit 保留(exit 2)。
# 去掉：dev 自动创建、amend、message 三重校验。
set -euo pipefail
# BRANCH 固定 dev（sync-17）：git-works-push 契约只负责 dev 分支，环境变量
# 覆盖（GIT_WORKS_BRANCH）可把推送目标改为任意分支且无调用方/文档引用，删除
BRANCH="dev"
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

# 入口锚定仓库根（sync-09）：harness/lib/commit_scope.py、log_prune.py 等
# 相对路径调用以仓库根为基准，子目录运行时静默不可达（收据比对降级、日志
# 清理失效）；非 git 仓 fail-closed（cd 失败即拒，防 git 操作打到错误目录）
TOPLEVEL="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "error: 不在 git 仓库内（无法定位仓库根），拒绝执行" >&2
  exit 1
}
cd "$TOPLEVEL" || { echo "error: 无法进入仓库根 $TOPLEVEL" >&2; exit 1; }

# 运行日志：harness/log/git-works-push/git-works-push-<日>.log（日粒度
# 追加，/harness/log/ 已 gitignore 不入库）。秒级时间戳文件名会让每次
# push 都新开文件、目录无限膨胀难追溯——同日多次 push 追加同文件，每行
# 前缀时间戳保证逐条时间归因；留存由 harness/lib/log_prune.py 清理。
# 目录锚定被操作仓库根（CDP_PROJECT_ROOT 优先，与 cdp_timing 打点根
# 同源；否则 git 工作树根）：脚本被测试 fixture 复用（cwd/CDP_PROJECT_ROOT
# 指临时仓）时日志随操作对象落盘，防污染真仓日志目录
if [ -n "${CDP_PROJECT_ROOT:-}" ]; then
  ROOT="$CDP_PROJECT_ROOT"
else
  ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
LOG_DIR="$ROOT/harness/log/git-works-push"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/git-works-push-$(date +%Y%m%d).log"
out() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"; }
err() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE" >&2; }

# push 段细分自发打点（A-1/B-1：脚本自发替代 AI 手动 mark push；细分
# commit/remote 两步，157s 级 push 段内部耗时不再不可归因）。失败静默
# 不阻断（打点诊断数据）。CDP_TIMING_T0 在脚本启动时取值供 push 总耗时。
CDP_TIMING_T0=$(date +%s.%N)
cdp_mark() {
  python3 "$SCRIPT_DIR/../cross-device/lib/python/cdp_timing.py" mark "$@" \
    >/dev/null 2>&1 || true
}

# 永不推 main 守卫
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  err "error: 禁止推送到 $BRANCH"; exit 1
fi
CUR=$(git branch --show-current)
if [ -z "$CUR" ] || [ "$CUR" != "$BRANCH" ]; then
  err "error: 当前分支 $CUR 非 $BRANCH（含 detached HEAD），禁止提交"; exit 1
fi

# 方向 3：幂等把 core.hooksPath 指向仓内 .githooks（commit-msg 中文前缀
# 校验），堵裸 git commit / 外部 skill 提交路径绕过脚本内校验的口子；
# 每次启动重复设置同值无害（幂等），钩子文件随仓入库（hooksPath 为本机
# .git/config 配置，不入库，故须每次启动确保接线）
HOOKS_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)/.githooks"
if [ -d "$HOOKS_DIR" ]; then
  git config core.hooksPath "$HOOKS_DIR"
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

# 发布内容与验证内容绑定（批次 261f10265269 方向 2）：实际提交面 vs 最新
# 收据 commit_scope，不一致即拒（两侧同排除 data/verify-results/——收据随批
# 入库且 scope 生成时排除自身目录）。无收据/旧收据缺字段 → warn 跳过（人工
# push 场景不阻断）。normal（staged 暂存面）与 push-only（origin..HEAD 待推
# 提交面）两种模式下均生效。
check_commit_scope() {
  local status_out
  if [ "$1" = "staged" ]; then
    status_out=$(git diff --cached --name-status --no-renames) || status_out=""
  else
    status_out=$(git diff --name-status --no-renames "origin/$BRANCH"..HEAD 2>/dev/null) || status_out=""
    [ -n "$status_out" ] || return 0   # 无待推提交，无比对对象
  fi
  local latest_scope diffs
  # commit_scope.py 依赖 cdp_receipt（cross-device lib）——脚本相对定位注入
  # PYTHONPATH（真仓），内部亦有自身定位兜底注入
  CDP_LIB="$SCRIPT_DIR/../cross-device/lib/python"
  latest_scope=$(PYTHONPATH="$CDP_LIB" python3 harness/lib/commit_scope.py --latest-scope 2>/dev/null) || latest_scope=""
  [ -n "$latest_scope" ] || {
    # 无收据/旧收据缺 commit_scope：按提交面内容分流（门禁旁路封堵）——
    # 暂存面含 code/** 业务源码 → 拒（RECEIPT_MISSING：发布内容与验证
    # 内容绑定不得无收据旁路，须先经 /workspace-verify 产收据）；仅
    # docs/harness/*.md 等非业务路径 → 维持 warn 放行（文档/工具改动
    # 无需上板收据）；紧急人工场景经 LGW_ALLOW_NO_RECEIPT=1 逃生门降级
    # warn（醒目警告留痕，事后须补收据）。命令替换判非空而非管道 -q
    # （grep -q 早退 + pipefail 会把 SIGPIPE 误判为比对不命中）
    if [ "${LGW_ALLOW_NO_RECEIPT:-0}" = "1" ]; then
      err "warn: 逃生门 LGW_ALLOW_NO_RECEIPT=1 生效——无收据跳过提交面比对（紧急人工场景放行，请事后补 /workspace-verify 收据）"
      return 0
    fi
    if [ -n "$(printf '%s\n' "$status_out" | grep -E '^[AMD]+[[:space:]]+code/')" ]; then
      err "error: RECEIPT_MISSING 无收据 commit_scope 且提交面含 code/ 业务源码（发布内容与验证内容绑定被旁路），请先经 /workspace-verify 产收据后再推送；紧急人工场景可设 LGW_ALLOW_NO_RECEIPT=1"
      exit 1
    fi
    echo "warn: 无收据 commit_scope（未走 ws_report 或旧收据），跳过提交面比对" >&2
    return 0
  }
  # 纯非业务提交面（无 code/ 项）→ 对账降级 warn：发布内容与验证内容的绑定
  # 语义只约束业务内容；业务收据已随上批入库推送后，后续 harness/docs-only
  # 工具性提交不再被旧业务收据卡住。提交面含 code/ 时仍强对账（业务整面一致）。
  if [ -z "$(printf '%s\n' "$status_out" | grep -E '^[AMD]+[[:space:]]+code/')" ]; then
    echo "warn: 提交面不含 code/ 业务文件，跳过与收据 commit_scope 比对（非业务改动无需上板收据）" >&2
    return 0
  fi
  diffs=$(printf '%s\n' "$status_out" | python3 harness/lib/commit_scope.py --check "$latest_scope") || true
  if [ -n "$diffs" ]; then
    err "error: 实际提交面与最新收据 commit_scope 不一致（发布内容与验证内容绑定），请核对或重写收据："
    printf '%s\n' "$diffs" >&2
    exit 1
  fi
}

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
  # 提交面收窄（防 git add -A 误吞运行态/本地产物）：已跟踪修改全收（add -u）；
  # 未跟踪仅白名单命中项随批入库，名单外拒绝并列出（请删除、gitignore 或评审后扩名单）。
  # 白名单 = 源码/证据目录前缀（case glob 跨 /，前缀锁定目录）+ 根目录精确名：
  # 运行态（harness/log 等）已被 gitignore 挡在 ls-files 之外，不会到这里的判定；
  # 根目录散文件（临时 msg、tar 包等）不在名单，仍拒绝（requirements.txt 为
  # 持久依赖声明，与 .github/* 同类精确放行）
  git add -u || { err "error: git add（已跟踪改动）失败"; exit 1; }
  UNTRACKED_ALLOW=(
    'data/verify-results/*'
    'data/baselines/*'
    'data/known-issues/*'
    'harness/*'
    'code/*'
    'docs/*'
    '.github/*'
    '.githooks/*'
    'requirements.txt'
  )
  REJECT=()
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    # --message-file 消息文件是提交输入而非提交对象，豁免白名单判定
    if [ -n "$MSG_FILE" ] && [ "$f" -ef "$MSG_FILE" ]; then continue; fi
    keep=""
    for pat in "${UNTRACKED_ALLOW[@]}"; do
      case "$f" in $pat) keep=1; break ;; esac
    done
    if [ -n "$keep" ]; then
      git add -- "$f" || { err "error: git add $f 失败"; exit 1; }
    else
      REJECT+=("$f")
    fi
  done < <(git ls-files --others --exclude-standard)
  if [ ${#REJECT[@]} -gt 0 ]; then
    err "error: 未跟踪文件不在提交白名单（${UNTRACKED_ALLOW[*]}），不在本批提交面："
    printf '  %s\n' "${REJECT[@]}" >&2
    exit 1
  fi
  # 凭据扫描：暂存区新增行命中敏感赋值（含低熵 psk 形态，键词+赋值+值 4 字符起）即拒；
  # 占位符白名单（placeholder/change_me/your_/xxxx/变量引用等）放行
  # （BL-20260624-01 教训：wifi.conf 真实 psk 曾随批入库）
  LEAK=$(git diff --cached -U0 | grep '^+' | grep -v '^+++' \
    | grep -iE '\b(psk|password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)\b[[:space:]]*[=:][[:space:]]*["'"'"']?[A-Za-z0-9/+=._-]{4,}' \
    | grep -viE 'placeholder|change[-_]?me|replace[-_]?me|your[_-]|xxxx|todo|fixme|dummy|sample|example|\$\{' || true)
  if [ -n "$LEAK" ]; then
    err "error: 暂存区新增行疑似凭据（psk/password/secret/token/key 赋值，占位符除外），请核实或改用占位符："
    printf '%s\n' "$LEAK" >&2
    exit 1
  fi
  check_commit_scope staged
  git commit -F "$MSG_FILE" || { err "error: commit 失败"; exit 1; }
  cdp_mark --name push_commit
fi

# push 失败分类：non-fast-forward（远端领先）给出可恢复提示，其余给原始输出
# push-only 模式在推送前同样做提交面比对（对 origin..HEAD 的待推提交，
# 与 normal 模式共用 check_commit_scope——两种模式下均生效）
if [ "$MODE" = "push-only" ]; then
  check_commit_scope pushed
fi
PUSH_T0=$(date +%s.%N)
push_dur() { awk -v a="$1" -v b="$(date +%s.%N)" 'BEGIN{printf "%.3f", b-a}'; }
if ! PUSH_OUTPUT=$(git push -u origin "$BRANCH" 2>&1); then
  cdp_mark --name push_remote --dur-s "$(push_dur "$PUSH_T0")"
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
cdp_mark --name push_remote --dur-s "$(push_dur "$PUSH_T0")"

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
# 方向 5：log_prune 接入真实工作流（此前零调用方顺延五批）——push 成功即
# 清理超龄 push 日志/timings 归档/promote 头快照（--apply 实际清理；失败
# 仅告警不阻断推送结果，留存规则见 harness/lib/log_prune.py）
if python3 harness/lib/log_prune.py --apply >/dev/null 2>&1; then
  out "log_prune: 运行日志清理完成"
else
  out "warn: log_prune 清理失败（不影响推送结果）"
fi
cdp_mark --name push --dur-s "$(push_dur "$CDP_TIMING_T0")"
exit 0

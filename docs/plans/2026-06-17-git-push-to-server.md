# git-push-to-server 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `/git-push-to-server` 命令，一键完成"收集 diff → AI 生成中文 type commit message → 单次确认（支持多轮编辑）→ 提交并推送"。

**Architecture:** 脚本+AI 混合（与现有 `sync-code-to-patchs` / `sync-patchs-to-doc` 分工一致）。两个 bash 脚本做机械工作（collect_diff.sh 收集 diff、commit_and_push.sh 执行提交推送），SKILL.md 指导 AI 做 message 生成与多轮编辑交互，command 中转壳子透传参数。

**Tech Stack:** Bash（`set -uo pipefail`、`SCRIPT_DIR`、颜色日志，与现有 skill 脚本风格一致）、Markdown（SKILL.md + command）。

**Spec:** [docs/specs/2026-06-17-git-push-to-server-design.md](../specs/2026-06-17-git-push-to-server-design.md)

---

## File Structure

| 文件 | 责任 | 动作 |
|------|------|------|
| `skills/git-push-to-server/collect_diff.sh` | 收集 git status + diff + 分支信息，格式化输出给 AI | Create |
| `skills/git-push-to-server/commit_and_push.sh` | `git add -A` + `git commit -F` + `git push`，失败保留 commit | Create |
| `skills/git-push-to-server/SKILL.md` | AI 工作流：message 规范 + 确认界面 + 多轮编辑交互 | Create |
| `.opencode/commands/git-push-to-server.md` | 中转壳子，透传参数 + 引用 SKILL.md | Create |

**测试策略说明**：本项目无自动化测试框架（bash skill 脚本不写单元测试），采用**手动验证 + 真实工作区场景**。每个脚本任务包含"手动验证"步骤，用真实 git 状态跑，验证输出/行为符合 spec。最后 Task 5 做端到端集成验证。

---

## Task 1: collect_diff.sh — diff 收集脚本

**Files:**
- Create: `skills/git-push-to-server/collect_diff.sh`

参考 spec §4（collect_diff.sh 接口）和现有 `sync_patchs_to_doc.sh` 的脚本风格。

- [ ] **Step 1: 创建目录**

```bash
mkdir -p skills/git-push-to-server
```

- [ ] **Step 2: 写 collect_diff.sh 完整实现**

```bash
cat > skills/git-push-to-server/collect_diff.sh <<'SCRIPT_EOF'
#!/bin/bash
set -uo pipefail

# ============================================================================
# collect_diff.sh — 收集 git status + diff + 分支信息，格式化输出给 AI
# 规则详见: skills/git-push-to-server/SKILL.md
# 用法:    bash skills/git-push-to-server/collect_diff.sh [--stat-only]
# 退出码:  0=有改动（正常输出）; 1=无改动（输出 nothing to commit）
# ============================================================================

# --- Configuration ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# diff 过大阈值
MAX_FILES=50
MAX_LINES=5000

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
STAT_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --stat-only) STAT_ONLY=true ;;
        -h|--help)
            echo "Usage: bash skills/git-push-to-server/collect_diff.sh [--stat-only]"
            echo "  --stat-only  只输出分支 + status + --stat，不输出 diff 正文"
            exit 0 ;;
        *) log_error "未知参数: $arg"; exit 1 ;;
    esac
done

# ============================================================================
# 前置检查
# ============================================================================
cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; exit 1; }

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
    exit 1
fi

# 文件数统计
FILE_COUNT=$(echo "$STATUS_OUTPUT" | grep -c '.' || true)

# ============================================================================
# 输出格式化
# ============================================================================
log_step "GIT PUSH CONTEXT"
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
    git status --short | awk '{print $NF}' | sed 's/^/ /' || true
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
                git --no-pager diff HEAD -- "$f" 2>/dev/null | head -20
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
                cat "$f" 2>/dev/null | sed 's/^/+/' || echo "(无法读取)"
                echo ""
            done
        fi
    fi
fi
echo "======================================"
SCRIPT_EOF
chmod +x skills/git-push-to-server/collect_diff.sh
```

- [ ] **Step 3: 手动验证 — 当前工作区跑（有改动）**

```bash
bash skills/git-push-to-server/collect_diff.sh
```
Expected: 输出包含 "GIT PUSH CONTEXT" 标题、当前分支、远程 URL、改动文件列表、--stat 统计、完整 diff 正文，退出码 0。

- [ ] **Step 4: 手动验证 — --stat-only 模式**

```bash
bash skills/git-push-to-server/collect_diff.sh --stat-only
```
Expected: 输出包含分支、status、--stat，但 diff 正文部分显示 "(已跳过 diff 正文)"，退出码 0。

- [ ] **Step 5: 手动验证 — 无改动场景**

```bash
git stash -u && bash skills/git-push-to-server/collect_diff.sh; STASH_RC=$?; git stash pop
echo "退出码: $STASH_RC"
```
Expected: 输出 "nothing to commit, working tree clean"，退出码 1。stash pop 恢复改动。

> ⚠️ 如果 `git stash -u` 影响未追踪的新文件（如本 skill 脚本本身），改用更安全的方式：临时 `git stash` 仅 tracked 改动验证，或跳过此步在 Task 5 集成验证时补做。

- [ ] **Step 6: 手动验证 — 帮助信息**

```bash
bash skills/git-push-to-server/collect_diff.sh --help
```
Expected: 输出 Usage 信息，退出码 0。

- [ ] **Step 7: Commit**

```bash
git add skills/git-push-to-server/collect_diff.sh
git commit -m "新增(skills): git-push-to-server collect_diff.sh 收集 diff 与分支信息

- skills: 新增 collect_diff.sh，输出 git status/diff/分支，支持 --stat-only 和大 diff 降级"
```

---

## Task 2: commit_and_push.sh — 提交推送脚本

**Files:**
- Create: `skills/git-push-to-server/commit_and_push.sh`

参考 spec §5（commit_and_push.sh 接口）。核心约束：push 失败保留 commit 不回退、禁止 force push。

- [ ] **Step 1: 写 commit_and_push.sh 完整实现**

```bash
cat > skills/git-push-to-server/commit_and_push.sh <<'SCRIPT_EOF'
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
SCRIPT_EOF
chmod +x skills/git-push-to-server/commit_and_push.sh
```

- [ ] **Step 2: 手动验证 — 帮助信息**

```bash
bash skills/git-push-to-server/commit_and_push.sh --help
```
Expected: 输出 Usage 信息，退出码 0。

- [ ] **Step 3: 手动验证 — 缺少必填参数报错**

```bash
bash skills/git-push-to-server/commit_and_push.sh
```
Expected: 输出 "[ERROR] 缺少必填参数 --message-file"，退出码 1。

- [ ] **Step 4: 手动验证 — 文件不存在报错**

```bash
bash skills/git-push-to-server/commit_and_push.sh --message-file /tmp/nonexistent.txt
```
Expected: 输出 "[ERROR] message 文件不存在: /tmp/nonexistent.txt"，退出码 1。

- [ ] **Step 5: 手动验证 — --no-push 实际提交（安全测试）**

创建临时改动 + 临时 message 文件，用 `--no-push` 提交，验证只 commit 不 push：

```bash
# 准备临时改动
echo "test marker $(date +%s)" > /tmp/git_push_test_marker.txt 2>/dev/null || true
# 写 message 到临时文件
cat > /tmp/git_push_test_msg.txt <<'MSG'
杂项(skills): 验证 commit_and_push.sh 的 --no-push 模式

- skills: 临时测试提交
MSG
# 执行（只 commit 不 push）
bash skills/git-push-to-server/commit_and_push.sh --message-file /tmp/git_push_test_msg.txt --no-push
echo "退出码: $?"
# 查看最新 commit
git log --oneline -1
```
Expected: 输出 "Step 1/2/完成（未推送）"，commit hash 显示，退出码 0，最新 commit message 显示"杂项(skills): 验证..."。

> ⚠️ 此测试会产生真实 commit。测试后立即回退：`git reset --soft HEAD~1`（改动回到暂存区），再 `git reset HEAD /tmp/git_push_test_marker.txt` 取消暂存，最后清理临时文件。

- [ ] **Step 6: 清理测试 commit**

```bash
git reset --soft HEAD~1
git reset
rm -f /tmp/git_push_test_msg.txt /tmp/git_push_test_marker.txt
git status --short
```
Expected: 工作区回到测试前状态（可能有 collect_diff.sh 等未提交改动，正常）。

- [ ] **Step 7: Commit**

```bash
git add skills/git-push-to-server/commit_and_push.sh
git commit -m "新增(skills): git-push-to-server commit_and_push.sh 执行提交与推送

- skills: 新增 commit_and_push.sh，git add -A + commit -F + push，push 失败保留 commit 不回退"
```

---

## Task 3: SKILL.md — AI 工作流定义

**Files:**
- Create: `skills/git-push-to-server/SKILL.md`

参考 spec §6（确认界面）、§7（参数清单），以及现有 `sync-code-to-patchs/SKILL.md` 的 frontmatter 格式。

- [ ] **Step 1: 写 SKILL.md 完整内容**

```bash
cat > skills/git-push-to-server/SKILL.md <<'SKILL_EOF'
---
name: git-push-to-server
description: 收集 diff → AI 生成中文 type commit message → 单次确认（支持多轮编辑）→ 提交并推送到 origin。
---

# git-push-to-server

一键完成"收集 diff → AI 生成规范化 commit message → 单次确认 → 提交并推送"，解决手动写 message 和推送的繁琐。

**核心语义**：脚本做机械工作（diff 收集、git add/commit/push），AI 做语义工作（理解 diff、生成 message、多轮编辑交互）。

## 工作流

### 1. 收集 diff（脚本）

```bash
bash skills/git-push-to-server/collect_diff.sh              # 完整输出（status + stat + diff）
bash skills/git-push-to-server/collect_diff.sh --stat-only  # 仅 status + stat，跳过 diff 正文
```

脚本输出当前分支、远程、git status、改动统计、diff 正文。无改动时输出 `nothing to commit` 并退出码 1，AI 见此**停止流程**。

**大 diff 降级**（>50 文件或 >5000 行）：脚本自动改为输出 `--stat` + 每个文件前 20 行摘要，末尾提示"diff 已截断"。AI 基于摘要生成 message。

### 2. 生成 message（AI，严格按规范）

读取 collect 输出，按下列规范生成 message。

#### 格式

```
<中文type>(<scope>): <subject>

<body bullet 列表>
```

#### 中文 type 词表

| type | 含义 | 典型场景 |
|------|------|---------|
| 新增 | 新功能/新特性 | 新增 lcview 字段、新增 iod 模块 |
| 修复 | bug 修复 | 修复打点崩溃 |
| 重构 | 不改行为的结构调整 | 脚本结构调整 |
| 文档 | 文档类改动 | specs、README、设计文档 |
| 杂项 | 工具/配置/脚本类 | skill、command、rules |
| 构建 | 构建系统改动 | mk_rpi5_full_image.sh、Android.bp |

#### scope 词表（目录+模块，改动行数最多目录为准）

| 目录 | 模块识别规则 | scope |
|------|------------|-------|
| `kernel/` 下 `vendor/lechao/LcView/**` | 路径含 `LcView` | `kernel-lcview` |
| `kernel/` 下 `vendor/lechao/LcIod/**` | 路径含 `LcIod` | `kernel-lciod` |
| `kernel/` 其他 | 无明确模块 | `kernel-unknown` |
| `aosp/` 下涉及 lcview/lciod | grep 文件名/路径 | `aosp-lcview` / `aosp-lciod` |
| `aosp/` 其他 | 无明确模块 | `aosp-unknown` |
| `docs/` | 固定 | `docs` |
| `skills/` | 固定 | `skills` |
| `rules/` | 固定 | `rules` |
| `scripts/` | 固定 | `scripts` |
| `.opencode/` | 固定 | `tooling` |
| 未命中 | 兜底 | `misc` |

#### 选取规则

- **type**：按改动主体语义选（新功能→新增、修 bug→修复、结构调整→重构、文档→文档、工具脚本→杂项、构建系统→构建）
- **scope**：改动行数最多的目录 + 模块
- **subject**：精炼描述主要改动，中文，不加句号
- **body**：bullet 列每个**有改动的目录**及摘要；`docs` 无改动则不列（避免无信息条目）

#### 示例

```
新增(skills): sync-code-to-patchs 支持删除对齐

- skills: 新增 sync_prune 函数实现 patchs 删除对齐
- rules: 更新 source-code-priority.md 镜像规则说明
```

### 3. 单次确认（AI 展示，支持多轮编辑）

展示格式（**只展示 type/scope/subject/body + 分支，不重复完整 message**）：

```
────────── 提交预览 ──────────
type:    新增
scope:   skills
subject: sync-code-to-patchs 支持删除对齐

body:
  - skills: 新增 sync_prune 函数实现删除对齐
  - rules: 更新镜像规则说明

分支: main → origin/main
──────────────────────────────
确认？(y 确认 / n 取消 / 或说明要改的地方)
```

#### 交互分支

| 用户输入 | AI 行为 |
|----------|---------|
| `y` | 调 `commit_and_push.sh` 执行 commit + push |
| `n` | 取消，不 commit |
| 文字描述修改意见（如 `scope 改成 tooling，body 第二条删掉`） | AI 按意见改 message，**重新展示**，再次确认（可多轮） |

### 4. 执行提交推送（脚本）

用户确认后，AI 将最终 message 写入临时文件，调脚本：

```bash
bash skills/git-push-to-server/commit_and_push.sh \
    --message-file <临时文件> \
    [--branch <分支>] \
    [--remote origin] \
    [--no-push]
```

脚本行为：`git add -A` → `git commit -F` → `git push <remote> <branch>`。

**push 失败处理**：脚本保留 commit 不回退（退出码 2），提示用户手动处理（`git push` 重试 / `git pull --rebase` / `git reset --soft HEAD~1` 回退）。**禁止 force push**。

## 参数清单

| 参数 | 说明 |
|------|------|
| 无参数 | 完整流程：collect → 生成 → 确认 → commit + push |
| `--dry-run` | 只 collect + 生成 message 展示，不 commit 不 push |
| `--no-push` | 确认后只 commit 不 push |
| `--branch <b>` | 指定推送分支（默认当前分支） |
| `--remote <r>` | 指定远程（默认 origin） |

> 注：`--dry-run` 由 AI 在工作流层处理（collect 后不进入 commit 步骤）；`--no-push` / `--branch` / `--remote` 透传给 `commit_and_push.sh`。

## 边界处理

| 场景 | 处理 |
|------|------|
| 无改动 | collect_diff.sh 输出 `nothing to commit` + 退出码 1，AI 停止流程 |
| diff 过大（>50 文件或 >5000 行） | collect 自动降级为 --stat + 每文件前 20 行摘要 |
| push 失败 | 保留 commit，脚本退出码 2，提示手动处理（不自动回退） |
| AI 生成失败 | 停下提示用户手动写 message |
| force push | 禁止（脚本不提供该能力） |
| 排除项（node_modules 等） | 依赖 `.gitignore`，脚本不重复造轮子 |

## 不做的事（YAGNI）

- 不做 hunk 级分组提交（违背"快速"初衷）
- 不做 `--amend`（与"禁止 force push"冲突）
- 不做 force push（防止历史覆盖）
- 不做 PR 创建（仅负责 commit + push）
SKILL_EOF
```

- [ ] **Step 2: 手动验证 — frontmatter 与现有 skill 一致**

```bash
head -5 skills/git-push-to-server/SKILL.md
echo "---对比现有 skill frontmatter---"
head -5 skills/sync-code-to-patchs/SKILL.md
```
Expected: 两者 frontmatter 格式一致（`---` 开头，`name:` + `description:`，`---` 结尾）。

- [ ] **Step 3: 手动验证 — 完整阅读无断链**

```bash
cat skills/git-push-to-server/SKILL.md | grep -E 'commit_and_push\.sh|collect_diff\.sh'
```
Expected: 所有脚本引用路径与实际文件名一致（`collect_diff.sh`、`commit_and_push.sh`）。

- [ ] **Step 4: Commit**

```bash
git add skills/git-push-to-server/SKILL.md
git commit -m "新增(skills): git-push-to-server SKILL.md 定义 AI 工作流

- skills: 新增 SKILL.md，含 message 规范（中文 type/scope 词表）、单次确认界面、多轮编辑交互、边界处理"
```

---

## Task 4: command 中转壳子

**Files:**
- Create: `.opencode/commands/git-push-to-server.md`

参考现有 `.opencode/commands/sync-code-to-patchs.md` 的格式：frontmatter（description）+ 脚本调用（`!`bash ... `$ARGUMENTS`）+ `@` 引用 SKILL.md。

- [ ] **Step 1: 写 command 中转壳子**

```bash
cat > .opencode/commands/git-push-to-server.md <<'CMD_EOF'
---
description: 收集 diff → AI 生成中文 type commit message → 单次确认（支持多轮编辑）→ 提交推送
---
收集 diff（参数透传）：
!`bash skills/git-push-to-server/collect_diff.sh $ARGUMENTS`

严格遵循完整工作流（生成 message → 单次确认 → commit + push）：
@skills/git-push-to-server/SKILL.md
CMD_EOF
```

- [ ] **Step 2: 手动验证 — 格式与现有 command 一致**

```bash
echo "=== 新建 ==="
cat .opencode/commands/git-push-to-server.md
echo ""
echo "=== 对比 sync-code-to-patchs ==="
cat .opencode/commands/sync-code-to-patchs.md
```
Expected: 两者结构一致——frontmatter 含 description、`!`bash 调用脚本透传 `$ARGUMENTS`、`@`引用 SKILL.md。

> 注：command 中转壳子只引用 `collect_diff.sh`（流程入口）。`commit_and_push.sh` 由 AI 在用户确认后按 SKILL.md 工作流调用，不在壳子里直接跑（因为它需要 message 文件参数，由 AI 动态生成）。

- [ ] **Step 3: Commit**

```bash
git add .opencode/commands/git-push-to-server.md
git commit -m "新增(tooling): git-push-to-server command 中转壳子

- tooling: 新增 .opencode/commands/git-push-to-server.md，透传参数并引用 SKILL.md"
```

---

## Task 5: 端到端集成验证

验证完整流程能正确工作。Task 1-4 已分别 commit 本 skill 的 4 个文件，本 Task 用**临时测试文件 + 工作区现有改动**验证，验证后清理临时文件。

- [ ] **Step 1: 验证 collect_diff.sh 收集当前工作区改动**

```bash
bash skills/git-push-to-server/collect_diff.sh
```
Expected: 输出当前分支（main）、远程（origin）、工作区现有改动（可能含 patchs/README、rules、docs/specs 等历史未提交项）出现在 status 里，diff 正文可读，退出码 0。

- [ ] **Step 2: 验证 commit message 生成质量（AI 模拟）**

基于 Step 1 的输出，AI 应按 spec 规范生成 message。人工核对要点：
- type 与改动主体语义匹配（如全文档改动→`文档`，新功能→`新增`）
- scope 取改动行数最多目录 + 模块
- subject 精炼描述，中文，不加句号
- body 列每个有改动目录的摘要，docs 无改动不列

- [ ] **Step 3: 验证确认界面展示格式**

AI 按 SKILL.md §3 格式展示（示意，实际内容由 Step 2 的真实改动决定）：

```
────────── 提交预览 ──────────
type:    文档
scope:   docs
subject: <具体 subject>

body:
  - docs: <摘要>
  - <其他有改动的目录>

分支: main → origin/main
──────────────────────────────
确认？(y 确认 / n 取消 / 或说明要改的地方)
```

人工核对：界面只展示 type/scope/subject/body + 分支，**不重复完整 message**。

- [ ] **Step 4: 验证 commit_and_push.sh 的 --no-push 模式（安全测试，不真推）**

用临时测试文件验证 commit 流程，不推送：

```bash
# 准备临时测试文件（确保有改动可提交）
mkdir -p /tmp/git_push_e2e && echo "e2e test $(date +%s)" > /tmp/git_push_e2e/marker.txt
cp -r /tmp/git_push_e2e ./
git add git_push_e2e/marker.txt
# 写 message 到临时文件
cat > /tmp/e2e_msg.txt <<'MSG'
杂项(misc): git-push-to-server e2e 验证测试文件

- misc: 临时测试文件，验证后删除
MSG
# 执行 --no-push 提交
bash skills/git-push-to-server/commit_and_push.sh --message-file /tmp/e2e_msg.txt --no-push
echo "退出码: $?"
git log --oneline -1
```
Expected: 输出 "Step 1/2/完成（未推送）"，commit hash 显示，退出码 0，最新 commit message 显示"杂项(misc): git-push-to-server e2e..."。

- [ ] **Step 5: 清理验证 commit**

```bash
# 回退临时测试 commit
git reset --soft HEAD~1
git reset
# 删除临时测试文件
rm -rf git_push_e2e /tmp/git_push_e2e /tmp/e2e_msg.txt
git status --short
```
Expected: 工作区回到测试前状态（git_push_e2e 目录已删除，临时 commit 已回退）。

- [ ] **Step 6: 最终提交（提交本实施计划文档）**

把本实施计划文档本身作为验证对象，用 commit_and_push.sh 真实提交并推送：

```bash
# collect 查看计划文档改动
bash skills/git-push-to-server/collect_diff.sh
# AI 基于输出生成 message（示意，实际由 AI 驱动时自动生成）
# 用户确认 y 后，写 message 文件并执行（这里手动模拟正式流程）
cat > /tmp/final_msg.txt <<'MSG'
文档(docs): git-push-to-server 实施计划

- docs: 新增 docs/plans/2026-06-17-git-push-to-server.md，含 5 个 Task 的逐步实施步骤与验证
MSG
bash skills/git-push-to-server/commit_and_push.sh --message-file /tmp/final_msg.txt
rm -f /tmp/final_msg.txt
```
Expected: commit + push 成功，GitHub origin/main 出现新 commit。

> ⚠️ 此步真实推送到 GitHub。执行前确认网络可达 origin。push 失败时 commit 保留，按脚本提示手动处理（`git push` 重试 / `git pull --rebase`）。

---

## Self-Review Checklist

实施完成后逐项核对：

- [ ] **Spec 覆盖**：
  - §1 文件结构 → Task 1-4 创建 4 个文件 ✓
  - §2 端到端流程 → SKILL.md 工作流 + Task 5 验证 ✓
  - §3 collect_diff.sh 接口 → Task 1 ✓
  - §4 commit_and_push.sh 接口 → Task 2 ✓
  - §5 commit message 规范 → Task 3 SKILL.md ✓
  - §6 确认界面 → Task 3 SKILL.md ✓
  - §7 参数清单 → Task 3 SKILL.md ✓
  - §8 边界处理 → Task 1/2 脚本 + Task 3 SKILL.md ✓
  - §9 验证方式 → Task 5 ✓

- [ ] **类型一致性**：脚本名 `collect_diff.sh` / `commit_and_push.sh` 在所有 Task 中拼写一致 ✓

- [ ] **无占位符**：所有步骤含完整代码，无 TBD/TODO ✓

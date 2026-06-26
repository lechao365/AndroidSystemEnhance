# lc-quick-fix-issue 工作流实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建 `lc-quick-fix-issue` harness workflow，实现"自由文本检视意见 → 自动分析→定位→诊断→修复→测试→零确认提交推送"的一键流程。

**Architecture:** 新增一个 harness workflow（命令入口 + WORKFLOW.md + detect_test_env.sh 脚本 + README.md），复用现有 git-push-to-server 的 commit_and_push.sh、harness_bootstrap.sh 公共库、harness_pythonpath() 路径 API。PYTHONPATH 直接复用 `harness_pythonpath`（已有事实源），脚本只负责构造 TEST_CMD。

**Tech Stack:** Bash（OBS-001/002 合规脚本）、Markdown（WORKFLOW.md 流程契约 + 命令入口）、harness 公共库（bootstrap + observability + path_util）。

**Spec:** `docs/specs/2026-06-26-lc-quick-fix-issue-design.md`

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh` | Create | 探测 TEST_CMD + 输出 PYTHONPATH（OBS 合规） |
| `engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md` | Create | 7 阶段流程契约 |
| `engineering/harness/workflows/lc-quick-fix-issue/README.md` | Create | workflow 入口说明 |
| `.opencode/commands/lc-quick-fix-issue.md` | Create | 命令触发入口 |
| `engineering/harness/workflows/README.md` | Modify | 文件清单增加 lc-quick-fix-issue 行 |

---

## Task 1: 创建 workflow 目录 + detect_test_env.sh

**Files:**
- Create: `engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh`

- [ ] **Step 1: 创建目录**

```bash
mkdir -p engineering/harness/workflows/lc-quick-fix-issue
```

- [ ] **Step 2: 写入 detect_test_env.sh**

```bash
#!/bin/bash
set -uo pipefail

# ============================================================================
# detect_test_env.sh — 探测测试命令(TEST_CMD)和 PYTHONPATH
# 规则详见: engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
# 输出:    STDOUT 两行
#            TEST_CMD=<命令>
#            PYTHONPATH=<冒号分隔路径>
# 退出码:  0=探测成功; 3=参数/环境错误（未找到测试目录或 PYTHONPATH 为空）
# ============================================================================

# --- 锚点 + 公共库（bootstrap 统一入口）-------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"

harness_init "detect_test_env"

# ============================================================================
# 参数解析
# ============================================================================
for arg in "$@"; do
    case "$arg" in
        -h|--help)
            echo "Usage: bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh"
            echo "  无参数。输出 TEST_CMD 和 PYTHONPATH 两行到 STDOUT。"
            harness_exit 0 ;;
        *) log_error "未知参数: $arg"; harness_exit 3 ;;
    esac
done

cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; harness_exit 3; }

# ============================================================================
# Step 1: 获取 PYTHONPATH（复用 harness_pythonpath，符合 PATH-001 DRY）
# ============================================================================
step_begin "获取 PYTHONPATH"

PYTHONPATH_OUT=$(harness_pythonpath)

if [ -z "$PYTHONPATH_OUT" ]; then
    log_error "harness_pythonpath 返回空（检查 harness-paths.conf 中 PYTHON_PATH_ROOTS）"
    harness_exit 3
fi

log_info "PYTHONPATH=$PYTHONPATH_OUT"
step_end 0

# ============================================================================
# Step 2: 探测 pytest 配置，构造 TEST_CMD
# ============================================================================
step_begin "探测测试配置"

TEST_CMD=""

# 优先级 1: pytest.ini
if [ -f "$REPO_ROOT/pytest.ini" ]; then
    # 提取 [pytest] 段的 testpaths（如果有）
    PYTEST_TESTPATHS=$(sed -n '/^\[pytest\]/,/^\[/p' "$REPO_ROOT/pytest.ini" \
        | grep -iE '^testpaths' | head -1 | cut -d= -f2 | tr -d ' ' || true)
    if [ -n "$PYTEST_TESTPATHS" ]; then
        TEST_CMD="python3 -m pytest $PYTEST_TESTPATHS -v"
    else
        TEST_CMD="python3 -m pytest -v"
    fi
    log_info "发现 pytest.ini，TEST_CMD=$TEST_CMD"

# 优先级 2: pyproject.toml [tool.pytest.ini_options]
elif [ -f "$REPO_ROOT/pyproject.toml" ]; then
    PYTEST_TESTPATHS=$(sed -n '/\[tool.pytest.ini_options\]/,/^\[/p' "$REPO_ROOT/pyproject.toml" \
        | grep -iE 'testpaths' | head -1 | sed 's/.*=.*\[\(.*\)\]/\1/' | tr -d '"' | tr ',' ' ' | tr -d ' ' || true)
    if [ -n "$PYTEST_TESTPATHS" ]; then
        TEST_CMD="python3 -m pytest $PYTEST_TESTPATHS -v"
    else
        TEST_CMD="python3 -m pytest engineering/ --tb=short -v"
    fi
    log_info "发现 pyproject.toml，TEST_CMD=$TEST_CMD"

# 优先级 3: setup.cfg [tool:pytest]
elif [ -f "$REPO_ROOT/setup.cfg" ]; then
    PYTEST_TESTPATHS=$(sed -n '/\[tool:pytest\]/,/^\[/p' "$REPO_ROOT/setup.cfg" \
        | grep -iE '^testpaths' | head -1 | cut -d= -f2 | tr -d ' ' || true)
    if [ -n "$PYTEST_TESTPATHS" ]; then
        TEST_CMD="python3 -m pytest $PYTEST_TESTPATHS -v"
    else
        TEST_CMD="python3 -m pytest engineering/ --tb=short -v"
    fi
    log_info "发现 setup.cfg，TEST_CMD=$TEST_CMD"

# 优先级 4: 无配置，检查是否有测试目录
else
    # 扫描 engineering/ 下是否有 tests/ 目录
    TEST_DIRS=$(find engineering/ -type d -name "tests" 2>/dev/null | head -5 || true)
    if [ -n "$TEST_DIRS" ]; then
        TEST_CMD="python3 -m pytest engineering/ --tb=short -v"
        log_info "无 pytest 配置，发现测试目录，TEST_CMD=$TEST_CMD"
    else
        log_error "未找到 pytest 配置，也未找到 engineering/*/tests/ 目录"
        harness_exit 3
    fi
fi

step_end 0

# ============================================================================
# 输出结果（两行，供 AI 解析）
# ============================================================================
echo "TEST_CMD=$TEST_CMD"
echo "PYTHONPATH=$PYTHONPATH_OUT"

log_result "探测完成" "TEST_CMD=$TEST_CMD" "PYTHONPATH=$PYTHONPATH_OUT"

harness_exit 0
```

- [ ] **Step 3: 设置可执行权限**

```bash
chmod +x engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
```

- [ ] **Step 4: 运行脚本验证输出**

```bash
bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
```

Expected: STDOUT 输出两行
```
TEST_CMD=python3 -m pytest engineering/ --tb=short -v
PYTHONPATH=<绝对路径列表>
```
退出码 0。

- [ ] **Step 5: 验证 PYTHONPATH 与 harness_pythonpath 一致**

```bash
# shellcheck source=../../lib/shell/harness_bootstrap.sh
source engineering/harness/lib/shell/harness_bootstrap.sh
# 对比 detect_test_env.sh 输出的 PYTHONPATH 和 harness_pythonpath() 的输出
diff <(bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh 2>/dev/null | grep '^PYTHONPATH=' | cut -d= -f2-) <(harness_pythonpath)
```

Expected: 无 diff 输出（两者完全一致）。

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
git commit -m "新增(workflows): lc-quick-fix-issue 探测脚本 detect_test_env.sh

- 复用 harness_pythonpath 输出 PYTHONPATH（PATH-001 DRY）
- 探测 pytest.ini/pyproject.toml/setup.cfg 构造 TEST_CMD
- 无配置时回退到扫描 engineering/ 下 tests/ 目录
- OBS-001/002 合规：bootstrap 接入、step/log/退出码规范"
```

---

## Task 2: 创建 WORKFLOW.md 流程契约

**Files:**
- Create: `engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md`

- [ ] **Step 1: 写入 WORKFLOW.md**

````markdown
---
name: lc-quick-fix-issue
description: 根据自由文本检视意见自动分析→定位→诊断→修复→测试→零确认提交推送的一键工作流。
---

# lc-quick-fix-issue

一键完成"解析检视意见 → 定位源码 → 诊断问题 → 设计修复 → 测试 → 零确认提交推送"，消除多轮人工编排开销。

**核心语义**：脚本做确定性工作（探测测试环境、git 提交推送），AI 做语义工作（理解检视意图、定位源码、设计方案、修复代码、调试）。

## Trigger（触发条件）

- 用户执行 `/lc-quick-fix-issue` 命令，或表达"根据检视意见修复问题"的意图
- 附带自由文本检视意见（`$ARGUMENTS`）

## Preconditions（前置条件）

1. 当前位于 git 仓库工作目录（项目根）
2. `detect_test_env.sh` 探测成功（退出码 0）
3. 当前 git 工作区干净（无未提交改动）—— 否则不启动，提示用户先处理

## Inputs（输入）

| 参数 | 来源 | 必填 |
|------|------|------|
| `$ARGUMENTS` | 用户输入的自由文本检视意见 | 是 |
| `TEST_CMD` | `detect_test_env.sh` 输出 | 自动 |
| `PYTHONPATH` | `detect_test_env.sh` 输出 | 自动 |

## Zero-confirmation gate（零确认门）

**调用本工作流即视为用户授权全部后续操作（分析→修复→测试→提交→推送），中间无确认点。**

这与 `git-push-to-server` 的"单次确认门"不同。`git-push-to-server` 要求提交前等待用户确认 message；本工作流在 Stage 7 直接调用 `commit_and_push.sh`，跳过确认门。

豁免理由：用户执行 `/lc-quick-fix-issue` 并附检视意见时，已表达明确的修复+提交意图，无需重复确认。

## 七阶段流程

### Stage 1：解析检视意见

**输入**：自由文本（`$ARGUMENTS`）
**输出**：结构化 issue 列表（内存中，不落盘）

将自由文本拆解为独立 issue，每个 issue 包含：

| 字段 | 说明 |
|------|------|
| `id` | 序号（ISSUE-1, ISSUE-2...） |
| `raw` | 原文摘录 |
| `intent` | 检视者意图（一句话） |
| `severity` | `critical` / `functional` / `robustness` / `style` |
| `keywords` | 用于源码定位的关键词（函数名、类名、变量名、错误消息） |

**规则**：
- 一条检视意见 = 一个 issue（不可合并语义不同的意见）
- 含"同样"/"类似"/"也是"的复数意见，拆分为独立 issue
- 无法理解意图时，标记 `intent=UNCLEAR`，不猜测

### Stage 2：定位源码

**输入**：issue.keywords
**输出**：每个 issue 关联的 `file:line` 列表

**策略**（可并行子 agent，按 PAR-001）：
- 使用 `grep` 搜索关键词（函数名、类名、变量名、错误消息）
- 使用 `glob` 按文件名模式匹配
- 使用 `read` 读取候选文件上下文
- 每个 issue 精确到 `file:line` 级别

**规则**：
- 跨多个文件的 issue，记录所有相关文件
- 定位失败（找不到源码）→ 标记 `LOCATE_FAILED`，该 issue 跳过修复

### Stage 3：诊断问题

**输入**：issue.intent + 源码上下文
**输出**：问题确认或否定

结合检视意图和源码实际行为，判断：

| 判定 | 含义 |
|------|------|
| `CONFIRMED` | 检视意见成立，源码确实存在问题 |
| `REJECTED` | 检视意见不成立（如检视者误解了代码逻辑），记录理由 |
| `PARTIAL` | 部分成立（如方向对但描述的根因有误），修正问题描述 |

**规则**：
- 必须读取完整函数/类上下文，不可只看片段
- `REJECTED` 需要明确的技术理由（如"此处已有 XXX 保护"）

### Stage 4：设计修复方案

**输入**：所有 CONFIRMED 和 PARTIAL issue + 源码上下文
**输出**：统一修复方案

**关键原则：结果导向，多 issue 统筹**

1. 按文件聚合 issue（同一文件的多个 issue 合并处理）
2. 识别 issue 间的代码重叠区域（同一函数/类被多个 issue 指出）
3. 设计统一修复方案，确保：
   - 不引入功能回退（必须理解现有逻辑才能改）
   - 重叠区域的修复满足所有相关 issue 的诉求
   - 最终所有 CONFIRMED 和 PARTIAL issue 都被覆盖

**禁止**：
- 未理解现有逻辑就改代码
- 引入新的硬编码（违反 PATH-001）
- 破坏现有测试

### Stage 5：设计测试用例

**REQUIRED SUB-SKILL**：使用 `superpowers:test-driven-development`

**输入**：修复方案
**输出**：测试用例列表

对每个修复点设计测试：
- **回归测试**：覆盖检视意见指出的问题场景
- **边界测试**：空值、边界条件、并发（如适用）
- **不破坏测试**：确认现有测试仍通过

**规则**：
- 测试用例先于修复代码设计（TDD）
- 若修复方案无法设计出可验证的测试 → 方案不充分，回到 Stage 4

### Stage 6：实施修复 + 运行测试

**输入**：修复方案 + 测试用例
**输出**：修复后的代码 + 测试结果

**流程**：
1. 按 SRC-001 规则，改动 workspace 下源码
2. 编写/更新测试用例
3. 运行测试：

```bash
PYTHONPATH=<探测值> <TEST_CMD>
```

4. 全部通过 → 进入 Stage 7
5. 失败 → 进入调试循环

**调试循环**（最多 3 次）：

**REQUIRED SUB-SKILL**：使用 `superpowers:systematic-debugging`

```
重试 1-3:
    1. 读取失败测试的完整 traceback
    2. 分析根因（不是症状）
    3. 修复
    4. 重跑全部测试
    5. 通过 → 进入 Stage 7
    6. 仍失败 → 继续

超过 3 次:
    - 回退所有改动 (git checkout -- .)
    - 输出失败报告
    - 退出码 1
```

### Stage 7：零确认提交推送

**输入**：修复后的代码（测试全通过）
**输出**：git push 结果

**执行**：

1. AI 生成 commit message（中文，遵循 `scope-mapping.yaml`）：

```
fix(<scope>): 根据 N 条检视意见修复 <简要描述>

<逐条列出 issue 及对应修复点>
```

> `<scope>` 按 `engineering/harness/config/scope-mapping.yaml` 规则判定（改动行数最多目录 + 模块）。

2. 将 message 写入临时文件，调用脚本（**跳过确认门**）：

```bash
MSG_FILE=$(mktemp)
cat > "$MSG_FILE" << 'EOF'
<commit message 内容>
EOF

bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh \
    --message-file "$MSG_FILE"
```

**失败处理**：
- push 失败（退出码 2）：commit 已保留，报告 push 失败原因
- 参数/环境错误（退出码 3）：报告原因

## Outputs / artifacts（输出/产物）

| 产物 | 位置 | 说明 |
|------|------|------|
| 代码修复 | workspace | git commit + push |
| 测试结果 | 终端输出 | PASS/FAIL 摘要 |
| 失败报告（如有） | 终端输出 | 未修复的 issue 列表 + 原因 |

## Failure / recovery（失败/恢复）

| 场景 | 退出码 | 处理 |
|------|--------|------|
| 全部 issue 修复并推送成功 | 0 | 正常完成 |
| 测试 3 次重试失败 | 1 | 已 `git checkout -- .` 回退改动，输出失败报告 |
| commit 成功但 push 失败 | 2 | 透传 git-push-to-server 退出码 2，commit 已保留 |
| 前置检查失败（探测失败、git 脏区） | 3 | 不启动流程，提示原因 |
| 无 CONFIRMED issue（全部 REJECTED 或 LOCATE_FAILED） | 4 | 输出分析结果，不修改任何代码 |

## Related policy IDs（关联规则 ID）

- `SRC-001`：workspace 源码改动优先级
- `OBS-001` / `OBS-002`：脚本维测（detect_test_env.sh 合规）
- `PATH-001`：路径管理（复用 harness_pythonpath，不硬编码）
- `PAR-001`：并行策略（Stage 2 可并行子 agent）

## 关联工作流

| 工作流 | 关系 |
|--------|------|
| `git-push-to-server` | Stage 7 直接调用 `commit_and_push.sh`，跳过确认门 |
````

- [ ] **Step 2: 验证 front matter 格式**

```bash
head -5 engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md
```

Expected: 包含 `---`、`name: lc-quick-fix-issue`、`description:`、`---`。

- [ ] **Step 3: Commit**

```bash
git add engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md
git commit -m "新增(workflows): lc-quick-fix-issue WORKFLOW.md 七阶段流程契约

- Stage 1-4: 解析检视意见→定位源码→诊断→设计修复方案（结果导向多issue统筹）
- Stage 5: 设计测试用例（引用 TDD skill）
- Stage 6: 实施修复+调试循环≤3次（引用 systematic-debugging skill）
- Stage 7: 零确认提交推送（豁免 git-push-to-server 确认门）
- 统一退出码: 0/1/2/3/4"
```

---

## Task 3: 创建 README.md

**Files:**
- Create: `engineering/harness/workflows/lc-quick-fix-issue/README.md`

- [ ] **Step 1: 写入 README.md**

````markdown
# lc-quick-fix-issue

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：根据自由文本检视意见自动修复代码并提交推送的一键工作流。
- **职责边界**：脚本做确定性工作（探测测试环境、git 提交推送），AI 做语义工作（理解检视意图、定位源码、设计方案、修复代码、调试）。
- **上下游依赖**：消费 `detect_test_env.sh`（探测）和 `git-push-to-server/commit_and_push.sh`（提交推送）；依赖 `config/scope-mapping.yaml`（commit scope）。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 触发命令 | 实际使用时 |
| [关联资源](#关联资源) | WORKFLOW.md、配置链接 | 深入理解时 |

## 目录说明

| 文件 | 职责 | 关键入口 |
|------|------|---------|
| `WORKFLOW.md` | workflow 契约：trigger / preconditions / inputs / 七阶段流程 / 零确认门 | 被 `.opencode/commands/lc-quick-fix-issue.md` `@` 消费 |
| `detect_test_env.sh` | 探测 TEST_CMD 和 PYTHONPATH | 由 workflow 编排，也可独立运行验证 |

## 使用方式

| 触发方式 | 说明 |
|---------|------|
| `/lc-quick-fix-issue <检视意见>` | 完整流程：分析→修复→测试→提交推送（零确认） |

```bash
# 独立运行探测脚本验证环境
bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `./WORKFLOW.md` | 完整流程契约 |
| 关联 workflow | `../git-push-to-server/commit_and_push.sh` | Stage 7 调用 |
| 关联配置 | `../../config/scope-mapping.yaml` | commit scope 判定规则 |
| 关联配置 | `../../config/harness-paths.conf` | PYTHON_PATH_ROOTS（PYTHONPATH 事实源） |
````

- [ ] **Step 2: Commit**

```bash
git add engineering/harness/workflows/lc-quick-fix-issue/README.md
git commit -m "新增(workflows): lc-quick-fix-issue README.md 入口说明"
```

---

## Task 4: 创建命令入口

**Files:**
- Create: `.opencode/commands/lc-quick-fix-issue.md`

- [ ] **Step 1: 写入命令入口**

```markdown
---
description: 根据自由文本检视意见自动分析→定位→诊断→修复→测试→零确认提交推送
---

!`bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh`

AI 根据 $ARGUMENTS（检视意见）和探测结果（TEST_CMD/PYTHONPATH），按工作流处理：
@engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md
```

- [ ] **Step 2: 验证命令文件格式（与现有命令一致）**

```bash
cat .opencode/commands/git-push-to-server.md
echo "---"
cat .opencode/commands/lc-quick-fix-issue.md
```

Expected: 两者的结构一致——front matter（description）+ 脚本调用（`!`bash...``）+ `@WORKFLOW.md` 引用。

- [ ] **Step 3: Commit**

```bash
git add .opencode/commands/lc-quick-fix-issue.md
git commit -m "新增(tooling): lc-quick-fix-issue 命令入口

- 调用 detect_test_env.sh 探测测试环境
- @ 引用 WORKFLOW.md 注入 AI 编排上下文"
```

---

## Task 5: 更新 workflows/README.md 文件清单

**Files:**
- Modify: `engineering/harness/workflows/README.md`

- [ ] **Step 1: 读取当前文件清单段落**

```bash
cat engineering/harness/workflows/README.md
```

确认 `## 目录说明` 表格当前有 4 行（git-push-to-server、sync-code-to-patchs、revert-code-from-patchs、sync-patchs-to-doc）。

- [ ] **Step 2: 在目录说明表格末尾增加 lc-quick-fix-issue 行**

在 `| [sync-patchs-to-doc](./sync-patchs-to-doc/)` 行之后，新增一行：

```markdown
| [lc-quick-fix-issue](./lc-quick-fix-issue/) | 根据检视意见自动修复代码→测试→零确认提交推送 | 脚本做确定性工作（探测/提交），AI 做语义工作（分析/定位/修复） | `detect_test_env.sh` → AI → `commit_and_push.sh` |
```

- [ ] **Step 3: 同步更新「上下游依赖」段落**

将第 10 行的：
```
- **上下游依赖**：被 `.opencode/commands/*.md`（4 份）通过 `@WORKFLOW.md` 注入 AI 上下文
```
改为：
```
- **上下游依赖**：被 `.opencode/commands/*.md`（5 份）通过 `@WORKFLOW.md` 注入 AI 上下文
```

- [ ] **Step 4: 同步更新「使用方式」段落**

在 `## 使用方式` 的 bash 示例块中，在 `# patchs → 文档同步` 之后新增：

```bash
# 根据检视意见自动修复并提交
# （通过 /lc-quick-fix-issue 命令触发，无需手动调用脚本）
```

- [ ] **Step 5: Commit**

```bash
git add engineering/harness/workflows/README.md
git commit -m "文档(workflows): README 同步新增 lc-quick-fix-issue 工作流清单

- 目录说明表新增 lc-quick-fix-issue 行
- 上下游依赖 commands 数量 4→5
- 使用方式新增注释说明"
```

---

## Task 6: 端到端验证

- [ ] **Step 1: 验证 detect_test_env.sh 在干净仓库运行**

```bash
git status --porcelain
bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
echo "退出码: $?"
```

Expected: 退出码 0，输出 TEST_CMD 和 PYTHONPATH 两行。

- [ ] **Step 2: 验证探测结果的正确性（用探测到的 PYTHONPATH 跑一次测试）**

```bash
# 提取探测结果
EVAL_OUT=$(bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh 2>/dev/null)
TEST_CMD=$(echo "$EVAL_OUT" | grep '^TEST_CMD=' | cut -d= -f2-)
PYTHONPATH_VAL=$(echo "$EVAL_OUT" | grep '^PYTHONPATH=' | cut -d= -f2-)

# 用探测到的环境跑测试（少量）
PYTHONPATH="$PYTHONPATH_VAL" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_nodes.py -v 2>&1 | tail -5
```

Expected: 测试正常运行且全部 PASS。

- [ ] **Step 3: 验证 OBS 合规（日志和 artifacts 落盘）**

```bash
ls -la engineering/output/log/detect_test_env/latest.log
ls -la engineering/output/log/detect_test_env/artifacts/ 2>/dev/null || echo "（无 artifacts，正常——探测脚本不产生中间产物）"
```

Expected: `latest.log` 存在且包含本次运行的日志。

- [ ] **Step 4: 验证 WORKFLOW.md front matter（如 validator 存在）**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh 2>&1 | tail -10
```

Expected: 无 lc-quick-fix-issue 相关的校验错误。

- [ ] **Step 5: 最终 commit（如有 docs 校验修复）**

```bash
git status --porcelain
# 如有改动：
git add -A && git commit -m "修复(workflows): lc-quick-fix-issue docs 校验修复" || echo "无需修复"
```

---

## Self-Review

### Spec coverage

| Spec 章节 | 对应 Task |
|-----------|----------|
| 4.1 交付物清单（4 个文件） | Task 1-4 |
| 5.1 detect_test_env.sh | Task 1 |
| 5.2 WORKFLOW.md 七阶段 | Task 2 |
| 5.3 命令入口 | Task 4 |
| 4.4 与 git-push-to-server 的关系 | Task 2 (Stage 7 + 零确认门段落) |
| 6.1 detect_test_env.sh 测试 | Task 1 Step 4-5, Task 6 Step 1-2 |
| 6.2 端到端验证 | Task 6 |
| 7 实现顺序 | Task 1-5 与 spec 7 一致 |
| README 同步 | Task 5 |

### Placeholder scan

无 TBD/TODO/placeholder。所有步骤包含完整代码或精确命令。

### Type consistency

- `detect_test_env.sh` 输出格式（`TEST_CMD=` / `PYTHONPATH=`）在 Task 1 和 Task 2 WORKFLOW.md Stage 6 中一致
- `commit_and_push.sh --message-file` 参数签名与 git-push-to-server 实际实现一致（Task 2 Stage 7）
- `harness_pythonpath` API 名称与 `harness_path_util.sh` 第 121 行定义一致
- `harness_init "detect_test_env"` 与 OBS-001 的 `harness_init "<script-name>"` 签名一致

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-26-lc-quick-fix-issue.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

# lc-quick-fix-issue 工作流设计

| 项目 | 值 |
|------|-----|
| 文档状态 | Draft |
| 创建日期 | 2026-06-26 |
| 作者 | AI 辅助生成 |
| 关联规则 | SRC-001, OBS-001, OBS-002, PATH-001, PAR-001 |
| 关联工作流 | git-push-to-server |

## 1. 背景与动机

在 loop runtime 引擎的多轮代码检视修复过程中（见会话记录 commit `fce9037` 等），存在一个高度重复的 7 步工作模式：

1. 接收自由文本检视意见
2. 理解每条意见的意图
3. 定位对应源码（`grep`/`glob`/`read`）
4. 结合源码诊断问题是否成立
5. 设计修复方案（多 issue 统筹，不引入回退）
6. 设计测试用例并实施修复
7. 运行测试 → 通过后提交推送

这个流程每轮重复一次，消耗大量交互。`lc-quick-fix-issue` 工作流将这个模式固化为一键触发的自动化流程，将人类从机械的编排中解放出来，专注于检视意见本身的质量。

## 2. 目标

- **一键修复**：用户输入 `/lc-quick-fix-issue <检视意见>`，skill 自动完成分析→定位→诊断→修复→测试→提交→推送
- **零确认提交**：调用本工作流即视为授权提交推送，中间无确认点
- **多 issue 统筹**：自由文本中的多条检视意见全部修复后一次提交
- **自动调试重试**：测试失败时自动加载 systematic-debugging，最多重试 3 次
- **测试环境自动探测**：脚本化确定 `TEST_CMD` 和 `PYTHONPATH`，不依赖 AI 每次推理
- **不引入回退**：修复前必须充分理解现有逻辑，方案设计阶段要有诊断确认

## 3. 非目标（YAGNI）

- 不做 hunk 级或 issue 级的独立提交（违背"全部合并一次提交"诉求）
- 不做 PR 创建（仅 commit + push）
- 不做 issue 依赖图拓扑排序（采用"结果导向"，只要最终全部通过即可）
- 不做检视意见的结构化模板输入（自由文本即可）
- 不做修复方案的预确认（全流程零确认）

## 4. 架构

### 4.1 交付物清单

| 文件 | 类型 | 职责 |
|------|------|------|
| `.opencode/commands/lc-quick-fix-issue.md` | 命令入口 | 触发入口，调用探测脚本 + 引用 WORKFLOW.md |
| `engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md` | 流程契约 | 7 阶段编排指令（分析→定位→诊断→方案→测试→修复→提交） |
| `engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh` | 脚本 | 自动探测测试命令和 PYTHONPATH（OBS-001/002 合规） |
| `engineering/harness/workflows/lc-quick-fix-issue/README.md` | 说明 | workflow 入口说明 |

### 4.2 整体流程

```
用户输入: /lc-quick-fix-issue <自由文本检视意见>
    │
    ▼
.opencode/commands/lc-quick-fix-issue.md  (命令壳子)
    │
    ├──► detect_test_env.sh  (输出 TEST_CMD + PYTHONPATH)
    │
    └──► @WORKFLOW.md        (AI 编排流程契约)
              │
              ├── Stage 1: 解析检视意见 → 结构化 issue 列表
              ├── Stage 2: 定位源码（grep/glob/read，PAR-001 可并行）
              ├── Stage 3: 诊断问题（结合源码 + 检视意图）
              ├── Stage 4: 设计修复方案（结果导向，多 issue 统筹）
              ├── Stage 5: 设计测试用例（加载 TDD skill）
              ├── Stage 6: 实施修复 + 运行测试
              │             └─失败─► [systematic-debugging] → 重试(≤3)
              └── Stage 7: git-push-to-server (零确认提交)
```

### 4.3 与现有规则的关系

| 规则 | 关系 |
|------|------|
| OBS-001 | `detect_test_env.sh` 通过 `harness_bootstrap.sh` 统一入口接入 |
| OBS-002 | 脚本使用统一退出码（0/1/3），临时产物通过 `harness_tmp_file` |
| SRC-001 | 修复改动的是 workspace 源码树（本项目的 `engineering/loop/`） |
| PATH-001 | 脚本内不硬编码路径，通过 `harness_path` 获取 |
| PAR-001 | Stage 2 多文件源码定位可并行子 agent |

### 4.4 与 git-push-to-server 的关系

Stage 7 直接调用 `commit_and_push.sh`。`git-push-to-server` 的 WORKFLOW.md 中"单次确认门"约束在本工作流中**被显式豁免**：WORKFLOW.md 中声明"调用 lc-quick-fix-issue 即视为用户授权提交推送，跳过确认门"。这不修改 `git-push-to-server` 本身，而是在本工作流层声明豁免语义。

## 5. 详细设计

### 5.1 detect_test_env.sh

#### 输入输出

```
输入: 无参数（自动以 REPO_ROOT 为起点）
输出: STDOUT 两行
    TEST_CMD=<命令>
    PYTHONPATH=<冒号分隔路径>

退出码:
    0 = 探测成功
    3 = 未找到任何测试目录（参数/环境错误）
```

#### 探测规则（按优先级）

1. **PYTHONPATH 构造**：直接调用 `harness_pythonpath`（复用 `harness-paths.conf` 中的 `PYTHON_PATH_ROOTS`，符合 PATH-001 DRY 原则）
   - 已有配置：`engineering/loop/core/python:...:contracts/python:controller/python:deploy/python`
   - 脚本无需自行扫描
2. **pytest 配置发现**：检测 `pytest.ini`、`setup.cfg`、`pyproject.toml` 中的 `[tool.pytest.ini_options]`
   - 若存在 `testpaths`，优先使用
3. **TEST_CMD 构造**：
   - 有 pytest 配置 → `python3 -m pytest <testpaths> -v`
   - 无配置但有测试目录 → `python3 -m pytest engineering/ --tb=short -v`（以 Python 工程目录为根）

#### OBS 合规

- 通过 `lib/shell/harness_bootstrap.sh` 统一入口接入
- 调用 `harness_init "detect_test_env"`
- 使用 `step_begin`/`step_end` 包裹扫描和构造阶段
- 使用 `log_result` 记录结构化结果
- 使用 `harness_exit [code]` 退出

### 5.2 WORKFLOW.md 七阶段流程

#### Trigger（触发条件）

- 用户执行 `/lc-quick-fix-issue` 命令，或表达"根据检视意见修复问题"的意图
- 附带自由文本检视意见

#### Preconditions（前置条件）

1. 当前工作目录为项目根（`~/workspace/`）
2. `detect_test_env.sh` 探测成功（退出码 0）
3. 当前 git 工作区干净（无未提交改动）—— 否则退出码 4

#### Inputs（输入）

| 参数 | 来源 | 必填 |
|------|------|------|
| `$ARGUMENTS` | 用户输入的自由文本检视意见 | 是 |
| `TEST_CMD` | `detect_test_env.sh` 输出 | 自动 |
| `PYTHONPATH` | `detect_test_env.sh` 输出 | 自动 |

#### Stage 1：解析检视意见

**输入**：自由文本
**输出**：结构化 issue 列表（内存中，不落盘）

AI 将自由文本拆解为独立 issue，每个 issue 包含：

| 字段 | 说明 |
|------|------|
| `id` | 序号（ISSUE-1, ISSUE-2...） |
| `raw` | 原文摘录 |
| `intent` | 检视者意图（一句话） |
| `severity` | `critical` \| `functional` \| `robustness` \| `style` |
| `keywords` | 用于源码定位的关键词 |

**规则**：
- 一条检视意见 = 一个 issue（不可合并语义不同的意见）
- 含"同样"/"类似"/"也是"的复数意见，拆分为独立 issue
- 无法理解意图时，标记 `intent=UNCLEAR`，不猜测

#### Stage 2：定位源码

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

#### Stage 3：诊断问题

**输入**：issue.intent + 源码上下文
**输出**：问题确认或否定

AI 结合检视意图和源码实际行为，判断：

| 判定 | 含义 |
|------|------|
| `CONFIRMED` | 检视意见成立，源码确实存在问题 |
| `REJECTED` | 检视意见不成立（如检视者误解了代码逻辑），记录理由 |
| `PARTIAL` | 部分成立（如方向对但描述的根因有误），修正问题描述 |

**规则**：
- 必须读取完整函数/类上下文，不可只看片段
- `REJECTED` 需要明确的技术理由（如"此处已有 XXX 保护"）

#### Stage 4：设计修复方案

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

#### Stage 5：设计测试用例

**REQUIRED SUB-SKILL**：加载 `test-driven-development` skill

**输入**：修复方案
**输出**：测试用例列表

对每个修复点设计测试：
- **回归测试**：覆盖检视意见指出的问题场景
- **边界测试**：空值、边界条件、并发（如适用）
- **不破坏测试**：确认现有测试仍通过

**规则**：
- 测试用例先于修复代码设计（TDD）
- 若修复方案无法设计出可验证的测试 → 方案不充分，回到 Stage 4

#### Stage 6：实施修复 + 运行测试

**输入**：修复方案 + 测试用例
**输出**：修复后的代码 + 测试结果

**流程**：
1. 按 SRC-001 规则，改动 workspace 下源码
2. 编写/更新测试用例
3. 运行 `PYTHONPATH=<探测值> $TEST_CMD`
4. 全部通过 → 进入 Stage 7
5. 失败 → 进入调试循环

**调试循环**（≤3 次）：

**REQUIRED SUB-SKILL**：加载 `systematic-debugging` skill

```
重试 1-3:
    1. 读取失败测试的完整 traceback
    2. 分析根因（不是症状）
    3. 修复
    4. 重跑全部测试
    5. 通过 → break → Stage 7
    6. 仍失败 → 继续

超过 3 次:
    - 回退所有改动 (git checkout -- .)
    - 输出失败报告
    - 退出码 1
```

#### Stage 7：零确认提交推送

**输入**：修复后的代码（测试全通过）
**输出**：git push 结果

**执行**：
1. 调用 `engineering/harness/workflows/git-push-to-server/commit_and_push.sh`
2. AI 生成 commit message（中文，遵循 `scope-mapping.yaml`）
3. **跳过确认门**：本工作流的调用即视为用户授权

**commit message 格式**：

```
fix(<scope>): 根据 N 条检视意见修复 <简要描述>

<逐条列出 issue 及对应修复点>
```

> `<scope>` 按 `engineering/harness/config/scope-mapping.yaml` 规则判定（改动行数最多目录 + 模块）。

**失败处理**：
- push 失败（退出码 2）：commit 已保留，报告 push 失败原因，退出码 2
- 参数/环境错误（退出码 3）：报告原因，退出码 3

#### Outputs（输出）

| 产物 | 位置 | 说明 |
|------|------|------|
| 代码修复 | workspace | git commit + push |
| 测试结果 | 终端输出 | PASS/FAIL 摘要 |
| 失败报告（如有） | 终端输出 | 未修复的 issue 列表 + 原因 |

#### Failure Handling（失败退出码）

| 退出码 | 场景 |
|--------|------|
| 0 | 全部 issue 修复并推送成功 |
| 1 | 测试 3 次重试失败，已回退改动 |
| 2 | commit 成功但 push 失败（透传 git-push-to-server 退出码 2） |
| 3 | 前置检查失败（探测失败、git 脏区） |
| 4 | 无 CONFIRMED issue（全部 REJECTED 或 LOCATE_FAILED） |

### 5.3 命令入口（.opencode/commands/lc-quick-fix-issue.md）

```markdown
---
description: 根据自由文本检视意见自动修复代码并提交推送（零确认）
---

!`bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh`

AI 根据 $ARGUMENTS（检视意见）和探测结果，按工作流处理：
@engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md
```

## 6. 测试策略

### 6.1 detect_test_env.sh 单元测试

| 场景 | 预期退出码 | 预期输出 |
|------|-----------|---------|
| 正常项目结构（有 tests/ + test_*.py） | 0 | TEST_CMD + PYTHONPATH 非空 |
| PYTHONPATH 来源 | 0 | 与 `harness_pythonpath()` 输出一致 |
| 有 pytest.ini | 0 | TEST_CMD 使用 ini 中的 testpaths |
| 无 pytest 配置 | 0 | TEST_CMD 使用默认 `pytest engineering/` |

### 6.2 WORKFLOW.md 端到端验证

通过实际使用验证（与 commit `fce9037` 相同的检视意见作为输入），确认：
- Stage 1 正确拆解多 issue
- Stage 6 测试通过
- Stage 7 成功提交推送

## 7. 实现顺序

1. `detect_test_env.sh`（脚本 + OBS 合规）
2. `WORKFLOW.md`（流程契约）
3. `.opencode/commands/lc-quick-fix-issue.md`（命令入口）
4. `README.md`（workflow 说明）
5. 同步更新 `engineering/harness/workflows/README.md` 文件清单
6. 端到端验证

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| AI 误判检视意见，修复方向错误 | 中 | 中 | Stage 3 诊断必须读完整上下文 + REJECTED 需技术理由 |
| 调试循环陷入死循环 | 低 | 高 | 硬上限 3 次，超过即回退 |
| 探测脚本误判测试命令 | 低 | 中 | 探测规则有明确优先级，输出可人工验证 |
| 零确认提交了错误代码 | 低 | 高 | 全部测试通过 + 全量测试是硬门禁 |
| 多 issue 修复引入冲突 | 中 | 中 | Stage 4 统筹设计，结果导向 |

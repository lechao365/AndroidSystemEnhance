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

这与 `lc-git-push-to-server` 的"单次确认门"不同。`lc-git-push-to-server` 要求提交前等待用户确认 message；本工作流在 Stage 7 直接调用 `commit_and_push.sh`，跳过确认门。

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

bash engineering/harness/workflows/lc-git-push-to-server/commit_and_push.sh \
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
| commit 成功但 push 失败 | 2 | 透传 lc-git-push-to-server 退出码 2，commit 已保留 |
| 前置检查失败（探测失败、git 脏区） | 3 | 不启动流程，提示原因 |
| 无 CONFIRMED issue（全部 REJECTED 或 LOCATE_FAILED） | 4 | 输出分析结果，不修改任何代码 |

## OBS 例外声明

`detect_test_env.sh` 的两行数据输出（`TEST_CMD=` / `PYTHONPATH=`）属于**数据流输出**（供 AI 解析的结构化数据），与 `collect_diff.sh` 的 diff 报告正文同语义。这两行允许裸 `echo`，不受 OBS-002 "禁止裸 echo 输出诊断信息" 约束（它们不是诊断信息，是数据接口）。

## Related policy IDs（关联规则 ID）

- `SRC-001`：workspace 源码改动优先级
- `OBS-001` / `OBS-002`：脚本维测（detect_test_env.sh 合规）
- `PATH-001`：路径管理（复用 harness_pythonpath，不硬编码）
- `PAR-001`：并行策略（Stage 2 可并行子 agent）

## 关联工作流

| 工作流 | 关系 |
|--------|------|
| `lc-git-push-to-server` | Stage 7 直接调用 `commit_and_push.sh`，跳过确认门 |

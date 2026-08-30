---
name: revert-modify-from-main-base
description: dev 持续 NG 且 emit 侧强模型无法修复时，人工回退 dev 到 main 基线并恢复开发板。
no_commit: true
stages:
  - research: "丢弃清单预览（确认门）"
  - plan: "reset + force push + revert 收据"
  - code: "code→workspace 同步回 main"
  - review: "恢复验证（模式 B）"
---
# revert-modify-from-main-base

> **仅限 apply 设备（本地 WSL2）运行**；永不自动触发，仅在 emit 侧强模型
> 多轮修复失败后由用户手动执行。

核心语义：人工确认丢弃清单后，dev 硬重置 origin/main + force push，写 revert 收据，
code→workspace 同步回 main 并跑一次恢复验证确保开发板可启动。
## Trigger（触发条件）
- 用户显式触发（dev 持续 NG 且正向修复无望）
## Preconditions（前置条件）
- origin/main 可达；dev 与 main 的分叉已确认无抢救价值
## Human confirmation gates（人工确认门）
- 预览模式列出 origin/main..dev 丢弃清单，用户显式确认后 --execute
## Outputs / artifacts（输出/产物）
- dev 重置到 origin/main（force push）；revert 收据（result: revert，含被丢弃提交清单）
- harness/log/revert-modify-from-main-base/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- force push 失败（exit 2）转人工；恢复验证失败说明 main 基线本身异常，
  人工介入（不得再次自动 revert）
## Related policy IDs（关联规则 ID）
- SRC-004（code dev/main 为恢复真相源）
---
## 工作流
1. 预览：bash harness/skills/revert-modify-from-main-base/revert_modify_from_main_base.sh
   （列丢弃清单，不改任何状态）
2. 确认后执行：... --execute --confirm <dev前12位>（token 不匹配即拒；reset --hard origin/main + force push + revert 收据随 dev 提交）
3. code→workspace 同步回 main 状态：
   python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py --auto
4. 恢复验证：拉起 @workspace-verify 模式 B（--target main --prefix revert；
   默认含 boot 验收），确保开发板恢复正常基线
## 退出码
0 成功（含预览模式）/ 1 git 操作失败（confirm 不匹配、工作树非空、收据写入失败等）/ 2 force push 或收据推送失败 / 3 参数错误
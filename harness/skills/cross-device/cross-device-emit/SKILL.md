---
name: cross-device-emit
description: emit 侧（远端）分析仓内上下文、生成 CDP 批次纯文本（-s/-sv），selfcheck 后交用户拷贝到 apply 设备。
no_commit: true
stages:
  - research: "precheck + 上下文组装"
  - plan: "强 LLM 分析并拆多轮"
  - code: "生成 CDP 文本 + selfcheck"
  - review: "输出批次"
---
# cross-device-emit

> **仅限 emit 设备（远端）运行**；本 skill 在 apply 设备也可见（repo 即 pack），
> 但 apply 设备不得触发它产出批次。

核心语义：远端强 LLM 基于仓内上下文（main..dev diff、涉及文件全量、最新 verify
收据、docs）产出 CDP 批次纯文本（-s/-sv），selfcheck 后交用户拷贝到 apply 设备。
## Trigger（触发条件）
- 用户在本仓 clone 上 git pull 后，准备发起新一轮跨设备修改
## Preconditions（前置条件）
- 工作树干净；本地 HEAD == origin/dev；上批收据已推送（cdp_emit_precheck.py）
## Human confirmation gates（人工确认门）
- 零确认（产出批次文本，不落盘不提交）
## Outputs / artifacts（输出/产物）
- 纯文本 CDP 批次（stdout，用户拷贝）；临时文件 harness/log/cross-device-emit/（gitignore）
## Failure / recovery（失败/恢复）
- precheck 不过：按 reason 处理（pull 失败网络/树脏/上批未推拒产）
- selfcheck 不过：AI 修批次后重跑
## Related policy IDs（关联规则 ID）
- CDP-001（契约成对修改）
---
> 注记：工作流命令统一以 python3 解释器书写，拷贝即用；若平台仅提供
> python 命令，请等价替换（python3 → python）。
## 工作流
1. precheck：python3 harness/skills/cross-device/lib/python/cdp_emit_precheck.py
2. 上下文组装（指引强 LLM）：
   - git diff main..dev（全量；聚焦时可看上批 batch_base..dev）
   - 涉及文件在 code/ 下的全量内容（modified 看 .diff、new 看全量）
   - 最新 data/verify 收据（失败现场摘录）
   - 相关 docs/ 章节
3. 产批：-s/-sv + base + 意图/验收/方向，总字符 450~500 为目标区间（硬上限 500）；
   不足 450 说明描述不清或应合并后续批次（backlog 见底时允许低于 450）；每批 6-7 个变更点；
   base 自动取 precheck 后 origin/dev HEAD 前 12 位
   （git rev-parse --short=12 origin/dev，勿手算）；复杂任务拆多轮，每轮注明后续轮次
4. selfcheck：python3 harness/skills/cross-device/lib/python/cdp_parse.py
   --role emit <批次临时文件>（必须 exit 0）；另须确认批次正文不含
   单双引号字符（' 与 "），如有则改述为描述性说法
5. 输出：纯文本批次，无包裹标记；产一批等一批，不并行产下一条
## 约束（禁止）
- emit 侧禁止 git commit/push、禁止修改 code/（流程纪律，无技术强制，违者评审回退）
- 批次正文禁用单双引号字符（' 与 "）：apply 侧写临时文件的方式不受 emit 控制，
  引号会被 shell 消费、多行被压平，致收据 batch_base 空、batch_id 与 emit 不符；
  需表达引号语义时改用描述性说法（如带引号的赋值写法「--role="apply"」
  改述为「role 为 apply」）
- 批次正文禁止写入 apply 不感知的阶段路线/优先级表述（如「先跑 X 再跑 Y」
  「A 失败才走 B」等流程编排）：apply 只按方向清单逐条编辑与验证，不执行
  批次内暗含的决策树；阶段路线由 apply/workspace-verify 的既有流程决定，
  批次只陈述目标与验收，路线歧义交 apply 现场判定
## 环路串行
- apply 执行 → 结果落地 → emit 复盘 → 产批 → 交付严格串行：批次锚定
  base 与行号，apply 一执行 origin/dev 即变，提前产批必被 exit 18 拒
  （base 不匹配整批拒绝回 emit 重产）
- 结果落地含 emit 侧 pull；复盘前须核对 git rev-parse HEAD 与
  git ls-remote origin refs/heads/dev 一致
## 规格自检（产批前两问）
- 一问：本变更点依赖目标环境何种状态，有无证据（收据/日志/上板结果）
- 二问：要改的函数或参数调用方是谁、被实际执行的路径是否覆盖、
  新增能力是否接线
## 可拷贝性
- 方向必须可精确拷贝执行：含全路径、行号、精确替换文本或枚举值
- 文档改动还须写明落地形态：独立成行或行内、插在哪个小节前后、
  示例具体内容
- 只给「注明」「补充」这类动作词会致表达形式做反且无信号
  （AI 无法确定落地形态，产出方向即偏离）
## 调查分流
- 影响本批的调查（决定变更点是否成立的待查事项）前置且阻塞产批，
  结论落地前不得产批
- 不影响本批的调查推到下一轮复盘窗口处理，不得拖慢当前批次
## 四项交付物（每轮复盘与交付）
- 上批复盘逐条取证：对上批每一条方向逐条给出取证
  （收据/日志/落地结果），不得笼统说已处理
- 漂移点回收：识别并回收上批遗留的漂移点
- 本批必要性论证：说明本批变更点为何必要、不做会怎样
- 进度估算：给出已落地点数/总点数与百分比、本批落地后的预计值

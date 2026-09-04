---
name: loop-engineering
description: 验证收敛会话管理：patience/total 双层计数、失败指纹归因、修复重试与退出协议（apply 拉起模式 A / 本地直起模式 B）。
no_commit: true
stages:
  - research: "session start / 复用活跃会话"
  - plan: "run 输出本轮 verify 执行指引"
  - code: "AI 执行 verify 工作流 + 失败分析修复 + done 记账循环"
  - review: "终结归因 + diagnose 诊断 + 末轮收据并入诊断"
---
# loop-engineering

> 跨设备场景（模式 A）由 cross-device-apply 拉起；本地场景（模式 B）用户/AI
> 直接触发，不经过 apply。两类场景同一状态机，仅入口与收尾不同。

核心语义：把"验证失败 -> 分析 -> 修复 -> 重跑"的循环纪律从 AI 会话上下文
（易丢失）转移到脚本状态（session.json 持久化）。**脚本做机械工作（记账/
指纹比对/计数/退出判定/报告骨架），AI 做语义工作（失败分析/修复编辑/归因
复核/诊断撰写）**。单次验证执行归 workspace-verify（loop 不执行验证，只管理收敛）。
## Trigger（触发条件）
- 模式 A：cross-device-apply 拆解 -sv 批次后拉起（--batch-file）
- 模式 B：本地验证需求（revert 恢复验证、手工回归、性能采集、环境冒烟），
  用户自然语言触发，AI 拆解为 --target/--case/--goal
## Preconditions（前置条件）
- apply 侧拉起：批次已解析、code/ 编辑完成
- 模式 B 独立性：不依赖 apply 任何组件（护栏脚本 cdp_validate_patch /
  gen-manifest 与库 cdp_receipt / cdp_parse 均为 harness 共享资产）
## Inputs（输入）
- 模式 A：--goal（批次意图）+ --batch-file（CDP 批次）
- 模式 B：--goal + --target <12hex|dev|main> + --case <verify-cases.yaml 标签>
## Human confirmation gates（人工确认门）
- 零确认（高危动作沿用 workspace-verify 内部确认门）
## Outputs / artifacts（输出/产物）
- harness/log/loop-engineering/session-<id>/（session.json + diagnosis.md，gitignore 工作态；
  配额 20 份目录级老化，仅删已终结会话）
- 终态回传：末轮收据路径 + result + 退出归因 + attempt 数（apply 收尾用）
## Failure / recovery（失败/恢复）
- 会话中断：session.json 存活即 status -> run 续跑（resume）
- session 丢失/损坏：退化为直接调 workspace-verify（基线行为，SKILL 明示回退）
- 指纹误判（漏判冻结）：total 护栏兜底；人工经 status 复核指纹轨迹
- 中间轮收据被收据老化淘汰：session 快照自洽，诊断仍可聚合
## Related policy IDs（关联规则 ID）
- 复用 cdp_receipt 收据链语义（latest-wins / 50 份老化 / 只增不删）
---
## 核心机制（机械层，脚本承担）

- **attempt 唯一推进锚 = 收据落盘后 done**：param_error 补参、rescue 救援、
  apply 编辑自愈均不产收据不耗轮次
- **双层计数**：patience（同一问题连续修复失败，指纹冻结才 +1，演化清零，
  上限 3）+ total_attempts（成本护栏，每轮 +1，上限 10）
- **失败指纹**：(失败阶段, verify 退出码, 归一化首错误行) 哈希；归一化剥
  时间戳/路径/地址/数字，防签名漂移误判演化
- **每轮归因五分类**：pass / task_fail（修复重试）/ env_fail（砖机三分法，
  不烧轮次提前退出）/ framework_error（脚本异常，**禁止重试**）/ param_error
  （不入 runs）
- **退出归因**：pass / task_unsolvable（patience 耗尽且冻结）/
  cost_cap_exceeded（total 达上限，报告各阶段推进轨迹）/ env_fail / framework_error

## 退出码（ws_session.py）
- 0 正常 / 1 会话状态错误（已终结再 done、收据读失败）/ 2 参数错误 / 3 session 文件非法

## 工作流（AI 语义层）
1. 起会话：
   python3 harness/skills/loop-engineering/ws_session.py start --goal "<目标>"
     [--batch-file <cdp>] | [--target <12hex|dev|main> --case <标签>]
   （幂等：同 goal+target 活跃会话自动复用）
2. 执行轮次（run 给出指引，AI 按 workspace-verify SKILL 步骤 1-6 执行）：
   python3 harness/skills/loop-engineering/ws_session.py run --session <json>
   （先产自描述产物再传给报告：ws_upload_tests --result-file 与
   ws_acceptance --result-file 落在本会话日志目录，ws_report PASS 经
   --acceptance-file/--unit-test-file 按产物核验（run_id/输入摘要/单调时间/全绿）
   （模式 A 链路耗时打点：apply 侧已 start 打点文件，本轮 verify 内部各阶段
   沿用同段名 mark（不追加轮次前缀，段名稳定供复盘按段统计），收据
   timings 可看出各段耗时；未 start 则跳过，warn 不阻断）
3. 收据落盘后记账：
   python3 harness/skills/loop-engineering/ws_session.py done --session <json>
     --receipt <收据路径> [--stage sync|build|unit_test|push|acceptance]
     [--error-line "<首错误行>"] [--attribution env_fail|framework_error]
   （AI 职责：砖机三分法证据齐全时显式 env_fail；harness 脚本 traceback 时
   显式 framework_error 并停环；每轮把修复动作摘要写进 session.json 的
   runs[].fix_action）
4. 失败轮：读收据失败现场（logcat/dmesg/串口摘录）-> 分析 -> 修复编辑 code/
   （改 .diff 跑 cdp_validate_patch.py；改 code/rpi5 跑 gen_manifest.py 护栏）
   -> 重跑步骤 2-3
   修复编辑打点（C3，与 B1 selfcheck 收口联动）：编辑开始前 mark
   edit_plan（同名自动 #N 序号）标记修复编辑起点；编辑完成无需手动收口
   ——selfcheck 开跑前自动补打 edit（同名 #N），修复编辑耗时计入 edit 相
   （此前散落 gap/other 不可归因）
   重跑前先做带预算连接探测（A2）：python3 harness/skills/workspace-verify/
   ws_adb_connect.py ensure --budget 60 —— 预算内不在线按 env_fail 归因
   （砖机三分法）进入下一轮 patience，不进入验收长等待
5. 终结：status 确认归因 ->
   python3 harness/skills/loop-engineering/ws_session.py diagnose --session <json>
   -> task_unsolvable/cost_cap_exceeded 时 AI 用 ws_report 以末轮同参重写终态
   收据（--body 含诊断全文，--result fail），交 apply 侧推送（模式 A）
   或直接汇报（模式 B）
## 禁止（边界纪律）
- 禁解析/编辑批次内容（批次契约归 apply）
- 禁写收据（收据只由 verify 单次执行终点产生；终态诊断收据经 ws_report 写）
- 禁 push/commit（上传归 apply/人工）
- 禁嵌套自愈循环（重试只有一层）
- 禁对 framework_error 重试（会把框架 bug 洗成 LLM 修不动的假象）

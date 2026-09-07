---
name: idle-hardening
description: 闲时加固会话：只消费 idle-eligible 队列（KIR-002 抖动自动入队 + 人工标记），复用 ws_lock 让路协议，预算内修复 + 断点续跑 + 报告。适用于无人值守闲时窗口。
no_commit: true
stages:
  - research: "取锁 + 让路标志 + 队列盘点 + 进度续跑"
  - plan: "AI 制定预算内修复计划"
  - code: "二分定位确定性复现 → 修改 → 原子步骤边界让路检查 → 进度落盘"
  - review: "N 次连续全量绿闭环 / 超预算停写报告"
---
# idle-hardening

> 闲时加固（后台低优先级任务）：只消费 idle-eligible 队列，不抢占正式任务。
> 规则见 [harness/rules/idle-hardening.md](../../rules/idle-hardening.md)
> （IDLE-001~007：队列准入 / 让路协议 / 修复纪律 / 预算 / 断点续跑）。

## Trigger（触发条件）
- 用户/人工给出显式闲时加固命令（必须带时间预算，如
  `--budget 30m 队列: harness/skills/idle-hardening/SKILL.md`）
- 闲时窗口（无正式批次进行中）
## Preconditions（前置条件）
- 分支 dev；ws_lock 双锁可取（正式任务未占用）
- 取锁即置让路标志（IDLE-004：本会话是让路对象）
- 进度文件存在时自动 resume（断点续跑）
## Human confirmation gates（人工确认门）
- 队列标记（kind=idle-eligible）由人工设定；AI 不自选战场
- 让路中断/超预算/修复闭环的结果以报告呈现
## Outputs / artifacts（输出/产物）
- 修复后的代码（经正式批次 cross-device-apply 推送，本 skill 不直接 push）
- 进度文件 harness/log/idle-hardening/session.json（gitignore）
- 报告 harness/log/idle-hardening/report-<条目>.md（完成/放弃/让路）
## Failure / recovery（失败/恢复）
- 会话中断：session.json 存活即 resume 续跑（断点续跑，IDLE-005）
- 让路中断：原子边界收敛干净工作树 + 释放锁，下次窗口续跑
- 修复闭环：N 次连续全量绿（IDLE-006）
## Related policy IDs（关联规则 ID）
- IDLE-001~007（见 idle-hardening.md）
- KIR-002 / KIR-006（抖动登记与闭环语义）
- CDP-DOD-001（检查器门禁三要件）
---
## 工作流
1. 取锁 + 让路标志：
   - `with ws_lock.verify_locks():` 取 workspace+device 双锁（复用，不可自造）
   - 取锁即确认本会话为让路对象；原子步骤边界检查让路标志
     `ws_lock.yield_requested()`，是则收敛让路（见步骤 5）
2. 队列盘点：读 data/known-issues 中 `kind ∈ {flake, idle-eligible}` 且
   未闭环条目；结合 session.json 进度（已完成/当前条目/预算余量）决定
   resume 或从队首继续
3. 修复（每条目遵守 IDLE-006 flake 修复纪律）：
   - 二分定位污染最小对 → 证明确定性复现（无重试稳定复现）→ 才修改
   - 禁止 sleep 重试 / xfail / 弱化断言
   - 修改后 `python3 harness/lib/selfcheck.py --mode full` 全量绿
4. 原子步骤边界（每条目修复/验证完成点）：
   - 检查让路标志：置位则完成当前步骤后让路（步骤 5）
   - 进度落盘 session.json（预算余量递减）
5. 让路收敛（IDLE-004）：
   - 完成当前原子步骤（不中断验证中途）
   - `git status --porcelain` 须为空；非空则清理/还原到干净（不得留脏树）
   - 释放锁 → `ws_lock.clear_yield()`
   - 写报告（未完成条目保持 open，进度可续跑）
6. 闭环判定（IDLE-006）：修复后连续 N=3 轮 full 全量绿 → 该条目标
   fixed/wontfix 并填 resolved_in（KIR-006 语义）；不足即保持 open
7. 报告（IDLE-007）：完成 / 超预算 / 让路中断均写报告（做了什么、证据、
   未做原因、预算余量）
## 预算
- 显式命令必须带 --budget；预算用尽立即停（当前原子步骤完成点）并写报告
- 预算记录在 session.json，resume 时从余量继续
## 退出码
- 0：队列消费完或达到预算内自然收口
- 2：参数错误（缺 --budget）
- 3：取锁失败（LockHeld，正式任务占用，让路等待下次窗口）

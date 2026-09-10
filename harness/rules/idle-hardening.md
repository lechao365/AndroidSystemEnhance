# Idle-Hardening 闲时加固规则（队列准入 + 让路协议 + 修复纪律 + 预算 + 断点续跑）

> **规则 ID**：`IDLE-001` ~ `IDLE-007`
> - **IDLE-001（队列准入）**：闲时加固只消费 idle-eligible 队列（data/known-issues
>   中 kind 属 flake 或 idle-eligible 且未闭环的条目），AI 不得自选战场。
> - **IDLE-002（入队来源）**：KIR-002 抖动（selfcheck 全量红单跑绿自动登记
>   kind=flake）**自动入队**；其余任务由人工标 kind=idle-eligible 入队。
> - **IDLE-003（时间预算）**：显式命令必须带时间预算（--budget），预算用尽
>   即停并写报告（进度已落盘，可续跑）。
> - **IDLE-004（让路协议）**：复用 ws_lock（workspace/device 双锁）；正式任务
>   （apply/verify）取锁失败即置让路标志（ws_lock.request_yield）；会话在原子
>   步骤边界检查让路标志（ws_lock.yield_requested），收敛到干净工作树后才
>   释放锁（ws_lock.clear_yield），**不得留脏树**。
> - **IDLE-005（断点续跑）**：进度落盘（session.json 记已完成条目/当前条目/
>   预算余量），会话中断可 resume 续跑。
> - **IDLE-006（flake 修复纪律）**：先二分定位污染最小对、证明确定性复现再改；
>   禁止 sleep 重试、xfail、弱化断言；修后须 N 次连续全量绿。
> - **IDLE-007（报告）**：完成 / 超预算放弃 / 让路中断均写报告（做了什么、
>   证据、未做原因）。

## 核心原则

闲时加固是**低优先级后台任务**：不抢占正式任务资源（锁/设备/工作树）、可被
让路随时中断、中断后可续跑。与正式批次（cross-device-apply）的资源边界以
ws_lock 双锁 + 让路标志强制隔离。

## 队列准入（IDLE-001 / IDLE-002）

| 来源 | 判定 | 入队方式 |
|------|------|---------|
| KIR-002 抖动 | selfcheck 全量红、单跑绿自动登记 kind=flake | 自动入队 |
| 其余任务 | 人工评审标 kind=idle-eligible | 人工标 |

消费对象 = data/known-issues 中 `kind ∈ {flake, idle-eligible}` 且
`status ∉ {fixed, wontfix}` 的条目。**AI 不得自行挑选清单外任务**（防自选战场
掩盖高优先缺陷、空耗闲时窗口）。

## 让路协议（IDLE-004）

```
apply/verify 取锁失败（LockHeld）
  └─ ws_lock.request_yield()          # 置让路标志
idle-hardening 会话原子步骤边界
  └─ ws_lock.yield_requested()?       # 检查让路标志
       ├─ 否 → 继续下一原子步骤
       └─ 是 → 完成当前步骤 → 收敛到干净工作树
               （git status 干净 + 临时文件清理）
             → 进度落盘 → 释放锁 → ws_lock.clear_yield()
```

- 原子步骤边界 = 单个条目修复/验证的天然断点（如一次全量自检、一次 commit）。
- 让路收敛**不得**中断在验证中途的原子步骤（须先完成当前步骤再让）。
- 释放锁前工作树必须干净（`git status --porcelain` 为空），**不得留脏树**
  ——脏树会污染后续正式批次 precheck（工作树不干净即拒批）。

## flake 修复纪律（IDLE-006）

修复 flake 类条目的硬性顺序：

1. **二分定位污染最小对**：确定触发抖动的两条用例/两段代码的最小组合
   （如 xdist 并发下 A 与 B 互踩），不臆测。
2. **证明确定性复现**：最小对在无重试下可稳定复现才进入修改；不能复现的
   抖动不得改（防止用错误修复掩盖真实竞态）。
3. 修改后**禁止**：sleep 重试掩盖、xfail 跳过、弱化断言（把失败判红改绿）。
4. **N 次连续全量绿**：修后连续 N（默认 3）轮 `selfcheck --mode full` 全量
   绿才算闭环，方可把该条目标 fixed/wontfix 并填 resolved_in。
5. 超预算即停：把当前二分进展、候选根因、剩余步骤写报告，条目保持 open。

## 禁止行为

| 禁止行为 | 原因 | 正确做法 |
|---------|------|---------|
| AI 自选战场（清单外任务） | 掩盖高优先缺陷、空耗闲时窗口 | 只消费 idle-eligible 队列（IDLE-001） |
| 无预算闲跑 | 闲时窗口被无限占用，抢占正式任务时机 | 显式命令必带 --budget（IDLE-003） |
| 让路中断留脏树 | 脏树污染后续正式批次 precheck 拒批 | 原子边界收敛、工作树干净才释放锁（IDLE-004） |
| sleep 重试 / xfail / 弱化断言修 flake | 掩盖真实竞态而非修复 | 二分定位 + 确定性复现 + 真修（IDLE-006） |
| 一次单跑绿即闭环 | 单跑绿只证明非确定性，未证明修复有效 | N 次连续全量绿才闭环（IDLE-006） |
| 中断丢进度 | 闲时窗口碎片化，重复劳动 | 进度落盘 session.json 支持断点续跑（IDLE-005） |

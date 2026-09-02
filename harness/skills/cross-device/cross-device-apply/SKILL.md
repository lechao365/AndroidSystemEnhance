---
name: cross-device-apply
description: 解析 CDP 批次 → 编辑 code/（dev）→ 拉起 workspace-verify（-sv）→ 拉起 git-works-push。
no_commit: false
stages:
  - research: "precheck + 批次解析"
  - plan: "AI 制定编辑计划"
  - code: "编辑 + manifest 重生成 + verify + push"
  - review: "核对推送结果"
---
# cross-device-apply

> **仅限 apply 设备（本地 WSL2）运行**（需 workspace 与开发板访问）。

核心语义：解析 CDP 批次（base 拒批门），按编辑载体规则改 code/（new 全量直改、
modified/*.diff hunk 内编辑+校验器），-sv 拉起 workspace-verify，统一经 git-works-push
推送（失败收据随批入库供 emit 分析）。
## Trigger（触发条件）
- 用户粘贴 emit 侧 CDP 批次文本
## Preconditions（前置条件）
- 当前分支 dev；工作树干净；批次 base == 本地 HEAD 前 12 位
  （--expect-base 比对，不匹配 exit 18 拒批，回 emit 重产）
## Human confirmation gates（人工确认门）
- 零确认；高危动作（整卡刷写/boot dd）由 workspace-verify 内部确认
## Outputs / artifacts（输出/产物）
- code/ 编辑结果 + 重生成 manifest.yaml + data/verify-results 收据（随批 commit 推送）
- 收据 header `timings` 字段：链路耗时打点（precheck/edit/verify 内部/push 各段，
  cdp_timing.py 采集，供 emit 定位耗时瓶颈；缺失仅 warn 不阻断）
- harness/log/cross-device/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- 编辑失败：AI 自愈（上限 3 次，仅批次编辑环节--验证轮次重试归 loop-engineering
  的 patience/total 计数，不在此列）；超限标 fail 继续，收据 fail
- diff 编辑后跑 cdp_validate_patch.py；verify 同步 git apply --check 失败走自愈
- verify 失败仍 push（失败收据供 emit 分析）；push 失败转人工
## Related policy IDs（关联规则 ID）
- CDP-001、SRC-001/002（修订后）
---
## 工作流
1. 接收批次：用户粘贴 → AI 存临时文件 harness/log/cross-device/batch-<ts>.cdp
   批次临时文件必须用 heredoc 写入且定界符加单引号以禁用展开（cat > <文件> <<'EOF' ... EOF）；
   禁止 echo 类写法（引号被吞、多行压成一行致批次结构损坏，收据 batch_base 空）
2. 门禁：git branch --show-current 须为 dev、git status --porcelain 须为空，否则停止
2b. 耗时打点 start（必做，失败仍不阻断主流程）：
    python3 harness/skills/cross-device/lib/python/cdp_timing.py start --batch-file <批次文件>
    （batch_id 从批次文件内部解析；打点文件 harness/log/cross-device/
    timings-<batch_id>.json，供后续各步骤 mark；start 失败仅提示，继续主流程）
3. precheck（含 base 拒批）：
   python3 harness/skills/cross-device/lib/python/cdp_parse.py --role apply --expect-base "$(git rev-parse --short=12 HEAD)" <批次文件>
   （exit 0 通过；17 在 apply 角色降级 WARN，16 预算超限仍 blocking；
    exit 18 = base 不匹配，整批拒绝回 emit；exit 3 = 参数/文件错误）
   通过后打点（必做）：cdp_timing.py mark --batch <batch_id> --name precheck
   （batch_id 取本步输出；未 start 时 mark 返 3 仅提示，不阻断）
4. 编辑：按批次意图/方向编辑 code/ 全目录：
   - code/rpi5/{aosp,kernel}/{new,modified}、code/rpi5/others、code/rpi-zero2w：全量文件直接编辑
   - modified/*.diff：hunk 内编辑（+ 行/已有 context），禁引入新 context；
     每个编辑过的 .diff 跑 cdp_validate_patch.py 做结构/语义校验（不传 --against，
     apply 语义校验由 sync_code_to_workspace.py 承担：其在 checkout base 后
     git apply --check，避免对已打旧补丁工作树校验产生假失败）：
     python3 harness/skills/cross-device/lib/python/cdp_validate_patch.py <diff 文件>
   - 涉及 code/rpi5 时：python3 harness/skills/cross-device/lib/python/gen_manifest.py
     （重生成 code/rpi5/manifest.yaml，patch↔workspace 结构映射；sync-workspace-to-code 已删除）
编辑完成打点（必做）：cdp_timing.py mark --batch <batch_id> --name edit
5. 分流：
   - -sv → 显式执行 /loop-engineering（模式 A）：
      打点（必做）：cdp_timing.py mark --batch <batch_id> --name verify_start
     python3 harness/skills/loop-engineering/ws_session.py start
       --goal "<批次意图>" --batch-file <批次文件>
     按 loop SKILL 执行收敛循环（run verify 工作流 → done 记账 → 失败分析
     修复重试，patience/total 上限退出）；loop 终结回传末轮收据+归因+attempt 数
     （session 丢失/异常时降级：直接执行 /workspace-verify 模式 A，基线行为）
     末轮收据正文必须含 CDP 原文 + 失败现场（--body；超限终结批并含诊断报告）
     loop 终结（收据落盘）后打点（必做）：cdp_timing.py mark --batch <batch_id> --name verify_end
- 收据落盘是进步骤 6 的前提：ws_report 返 2（如 -sv 缺 --acceptance、
      --log-since 非法等参数错误）即收据未落盘，必须补参重试，禁止无收据进步骤 6
   **收据 cases 自动落盘 + 禁改历史口径（2026-09-02 定）**：
   - -sv 批次走 loop 验证时，ws_acceptance 验收完成自动把实跑 case 标签写
     log_apply_dir()/cases-<batch_id>.json（batch 识别三级回落），ws_report
     未传 --case 自动探测补全（显式传参优先）——board pass 收据 cases 由此
     自动落盘，空 cases 不再卡 prepare 的 evidence-scope 推导。
   - **禁改历史收据文件**：收据一经落盘即证据，事后回填/改写属伪造证据链。
     board+pass 空 cases 由 ws_report 源头拒写（返 2）兜底；发现缺 cases 时
     只写新收据引用旧批次（-s 自检批 + 说明），禁止编辑旧收据补字段
     （2026-09-02 BL-20260902-01 发布被迫回填 7833c640079a 的教训）。
- -s → 写 skip 收据（先自检，证据随收据落地；rc 为主判据，缺 rc/任一非零即拒写）：
      SELFCHECK=$(python3 harness/lib/selfcheck.py)
      python3 harness/skills/workspace-verify/ws_report.py --batch-file <批次文件> --result skip --build skip --board skip --summary "<意图首句>（-s 无需上板）" --selfcheck "$SELFCHECK" --body <批次文件> [--timings-file harness/log/cross-device/timings-<batch_id>.json]
    （selfcheck.py 用 subprocess 直取 pytest/check_skill_refs 的 returncode：
     命令替换赋值会把 PIPESTATUS 重置为 0，shell 内联取 rc 恒零，禁回退内联写法）
    verify 无论 pass/fail，收据落盘后必须执行下一步骤（git-works-push）
6. 显式执行 /git-works-push（收据+代码统一 commit push）
   完成后打点收尾（必做，失败仍不阻断）：cdp_timing.py mark --batch <batch_id> --name push
   然后 cdp_timing.py finish --batch <batch_id>（生成段耗时 JSON 归档，供人工/emit 参考）
   （-sv 批次的收据 timings 由 workspace-verify 步骤 6 的 --timings-file 写入，
    apply 侧 finish 仅归档含 push 段的完整打点）
## 退出码
- 0 完成（含 fail 收据已推送）；自愈上限 3 次，超限写 fail 收据继续 push；
  2 参数错误（ws_report 返 2 收据未落盘，补参重试）；3 参数/文件错误（cdp_parse）；
  18 precheck 拒批（base 不匹配回 emit）

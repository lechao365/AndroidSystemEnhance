# Boot 诊断报告模板

> 本模板约束 AI（opencode）在收到 EvidenceBundle 后产出的诊断报告格式。
> 报告路径：`engineering/output/runs/<run-id>/diagnosis-report.md`（与 EvidenceBundle 同目录）。

## 报告结构

每份诊断报告必须包含以下 6 节，顺序固定：

````markdown
# Boot 诊断报告 - <run-id>

## 1. 结论
- 整体状态：FAIL/PASS
- 根因假设：<一句话>

## 2. 证据链
| 阶段 | 证据 | 引用 |
|------|------|------|
| reboot | <status, 耗时, stage_reached> | EvidenceBundle.cases[trigger_reboot] |
| zygote | <status, 输出预览> | EvidenceBundle.cases[zygote_running] |
| kmsg | <异常片段> | collector.kmsg output |

## 3. 根因分析
<详细分析，引用证据>

## 4. 修复建议（人工执行）
- 改动点 1：workspace/<路径>:<函数> → <建议>
- 改动点 2：...

## 5. 建议新增 case（人工 review 后加入 boot-success.yaml）
<可选，无建议则写"本次无新 case 建议">
```yaml
- id: <建议的 case id>
  command: "<建议的命令>"
  assert: {type: contains, value: "<期望值>"}
  ...
```

## 6. 循环终止建议
- 已 PASS → 无需继续
- FAIL 根因明确 → 建议范围 B 自动改码（需用户确认）
- FAIL 根因不明确 → 建议人工介入
````

## AI 行为约束

1. AI 读 EvidenceBundle 后**必须**按此模板产出报告，不得自创格式
2. 报告路径**必须**与 EvidenceBundle 同目录
3. 第 4 节修复建议**必须**具体到 workspace 文件路径和函数名，禁止笼统"检查 xx 模块"
4. 第 5 节 YAML 建议**必须**完整可粘贴（含 id/command/assert/severity/on_fail）
5. AI **不自动修改** boot-success.yaml，只给建议（G2 决策）

## 字段说明

| 字段 | 说明 |
|------|------|
| `<run-id>` | EvidenceBundle 的 `bundle_id`（如 `eb-a23b8614`） |
| `stage_reached` | reboot_and_wait 达到的阶段：`l1_boot_start` / `l2_init_ready` / `l3_verified` / `none` |
| `<证据引用>` | 指向 EvidenceBundle JSON 的路径，如 `cases[0].output_preview` |

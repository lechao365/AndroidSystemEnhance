# 本地直连开发交付物（Definition of Done）

> **规则 ID**：`LOCAL-DEV-001` / `LOCAL-DEV-002` / `LOCAL-DEV-003`
> 加载时机：在 apply 设备（本地 WSL2）直连用 LLM 开发、不走 cross-device-apply
> 批次流程时，先加载本规则（开工必读）。

## 语义总纲

在 apply 设备上直连用 LLM 开发、不走 cross-device-apply 是**合法路径，不禁止**。
本地直连开发与 CDP 批次等效，但交付物必须补 manual 收据——本规则定义其
完成判据，缺收据即被 `check_commit_coverage` 判红、`git-works-push` 拒推。

## LOCAL-DEV-001 manual 收据须覆盖本次全部非 meta 提交

- 本地直连开发的每次非 meta 提交（提交标题 type ∈ {新增/修复/重构/杂项}，
  基线发布/纯文档属 meta 豁免）的改动文件集，须被某份收据的 `commit_scope`
  覆盖，manual 与 CDP 同等。
- manual 收据经 `ws_report.py --manual <base>..<head>` 生成：从 git 区间自动
  回填 `commit_scope`（区间内非 meta 提交改动面），无需手工维护清单。
- 区间 base 缺省 = 最近 promoted baseline 的 `source_commit`，head 缺省 = HEAD，
  与 `check_commit_coverage` 判定起点同源。

## LOCAL-DEV-002 逐项三态自报

- manual 收据 `--body` 须逐项自报本次改动：每项以 `完成` / `部分` / `拒绝`
  之一开头（与 CDP-DOD-003 三态同口径），`拒绝` 须带理由；无三态前缀即判红。
- 自报项须覆盖本次全部非 meta 提交的改动（调用方 = 实际执行路径，验证 =
  单测用例/自检 rc/干净克隆判据），不得笼统写"已处理"。

## LOCAL-DEV-003 带 selfcheck 结果

- manual 收据必须带 `--selfcheck`（pytest 摘要 + 全部 `*_rc` 键，任一非零
  ws_report 拒写），证据随收据落地——本地直连开发不得绕过自检通道。

## 门禁

- `check_commit_coverage`（selfcheck 以 `commit_coverage_rc` 透出、CI/ws_report
  判红）：自最近 promoted baseline 起非 meta 提交须被某份收据 `commit_scope`
  覆盖，未覆盖即打印 `ws_report --manual` 补齐命令。
- `git-works-push` 拒推（RECEIPT_MISSING 且提交面含 `code/` 业务源码）时直接
  打印补齐命令，指引先产 manual 收据再推送。

## 补齐命令模板

```bash
python3 harness/skills/workspace-verify/ws_report.py \
  --manual <最近baseline>..HEAD \
  --result skip --build skip --board skip \
  --summary "<本地直连开发摘要>" \
  --selfcheck "<pytest 摘要与各 *_rc>" \
  --body <逐项自报文件>
```

（`--result/--build/--board` 按实际验证结果调整；上板验证批可走 `-sv` 批次流程）

## 违规后果

- 违反 LOCAL-DEV-001：本地直连开发提交无收据覆盖，验证链断裂无从追溯 →
  `check_commit_coverage` 判红、push 拒推。
- 违反 LOCAL-DEV-002：收据无法逐项归因，无法确认覆盖面 → 拒写/拒推。
- 违反 LOCAL-DEV-003：零自检通道敞开，改动未经质量门禁 → 拒写。

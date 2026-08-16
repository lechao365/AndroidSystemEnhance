# 源码改动优先级

> **规则 ID**：`SRC-001` / `SRC-002` / `SRC-003` / `SRC-004`
> - `SRC-001`：`~/workspace/` 是唯一参与编译的源码真相源，所有定制改动必须先改 workspace。
> - `SRC-002`：`code/`（除 `others/`）是 workspace 的单向受控归档目录，仅允许通过 `lc-harness-sync-code-to-patchs` 同步，严禁手动修改。
> - `SRC-003`：`code/others/` 不依赖 workspace，允许独立维护。
> - `SRC-004`：未完成证据化晋升（promoted baseline）的 code 资产**不得**宣称为 revert workflow 的恢复真相源；证据字段须按`harness/config/baseline-evidence-template.yaml` 填写并在 `harness/config/baseline-status.yaml` 登记。

## 核心原则

`~/workspace/` 是编译源码树（唯一参与编译），`code/` 是单向归档目录。改动必须从源头开始。

## 改动规则

| 目标 | 操作流程 |
|------|---------|
| `code/` 下 `kernel/`、`aosp/` 等（对应 workspace 源码） | **必须先改 `~/workspace/` 源码**，验证通过后通过 `lc-harness-sync-code-to-patchs` 命令（`/lc-harness-sync-code-to-patchs`）同步归档 |
| `code/others/`（无 workspace 备份的独立程序） | 直接在 `code/others/` 中编辑维护 |

> **"验证通过"的定义**（缺一不可，必须作为 baseline 证据落盘）：
> 1. **build**：增量编译成功（`make bootimage/systemimage/vendorimage`）→ `build_result`
> 2. **package**：打包镜像成功（`mk_rpi5_full_image.sh`）→ `package_result`
> 3. **board verify**：刷机上板，功能验证 OK → `board_verify`
> 4. **operator**：执行人/批准人 → `approved_by`
> 5. **timestamp**：验证完成时间 → `approved_at`
>
> 编译通过 ≠ 验证通过。未上板验证前禁止归档。证据须按`harness/config/baseline-evidence-template.yaml` 填写，并在对应 `harness/config/baseline-status.yaml` 登记，方可晋升为 promoted baseline。

## 禁止行为

除 `others/` 外，**`code/` 下的目录只允许通过 `lc-harness-sync-code-to-patchs` 命令（`/lc-harness-sync-code-to-patchs`）同步，严禁手动修改**。原因：

1. `code/` 不参与编译，直接改此处 workspace 源码不会更新，编译无法生效。
2. 下次执行同步归档时，手动改动会被 workspace 的最新状态覆盖。

## 归档纪律

| 禁止行为 | 原因 | 正确做法 |
|---------|------|---------|
| 未打包/未上板就执行 `lc-harness-sync-code-to-patchs` | 归档未验证代码，违反"验证后归档"原则 | 走完编译→打包→上板→验证全流程 |
| 调试 workflow 脚本 bug 时运行真实业务命令 | 业务命令有副作用（写 code/改 git 状态），污染归档状态 | 用 `--check-only` 验证脚本逻辑，业务命令等流程到位再执行 |

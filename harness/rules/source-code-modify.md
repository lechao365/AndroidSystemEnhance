# 源码改动优先级

> **规则 ID**：`SRC-001` / `SRC-002` / `SRC-003` / `SRC-004`
> - **SRC-001（修订）**：`code/`（dev 分支）是唯一改动源头；`~/workspace/` 是编译缓存。
>   所有定制改动必须先改 code/（经 cross-device-apply 或手工编辑），再经
>   workspace-verify 同步到 workspace 编译验证。手工调试允许临时改 workspace 试验，
>   但必须回填到 code/ 才能进入验证/推送链路（否则验证结果不代表 code 状态）。
> - **SRC-002（修订）**：workspace 是 code 的编译缓存镜像，由 workspace-verify /
>   sync-code-to-workspace 单向同步（code → workspace）；禁止把 workspace 改动
>   反向归档回 code（sync-workspace-to-code 已 deprecated，方向消亡）。
> - **SRC-003**：`code/others/` 不依赖 workspace，允许独立维护。
> - **SRC-004（修订）**：仅 promoted（main 分支基线）可作为恢复真相源；dev 迭代
>   状态以 data/verify 收据为准，未验证的 dev 改动不得宣称为基线；证据字段须按
>   harness/config/baseline-evidence-template.yaml 填写并在 harness/config/baseline-status.yaml 登记。

## 核心原则

`code/`（dev 分支）是唯一改动源头，`~/workspace/` 是编译缓存镜像（code → workspace 单向同步）。改动必须从源头开始。

## 改动规则

| 目标 | 操作流程 |
|------|---------|
| `code/` 下 `kernel/`、`aosp/` 等（对应 workspace 源码） | **必须先改 `code/`（dev 分支）**，经 `workspace-verify` 同步到 workspace 编译验证 → 收据随批 push dev → 验证 OK 后经 `sync-modify-to-main-base` 晋升 main |
| `code/others/`（无 workspace 备份的独立程序） | 直接在 `code/others/` 中编辑维护 |

> **"验证通过"的定义**（缺一不可，必须作为 baseline 证据落盘）：
> 1. **build**：增量编译成功（`make bootimage/systemimage/vendorimage`）→ `build_result`
> 2. **package**：打包镜像成功（`mk_rpi5_full_image.sh`）→ `package_result`
> 3. **board verify**：刷机上板，功能验证 OK → `board_verify`
> 4. **operator**：执行人/批准人 → `approved_by`
> 5. **timestamp**：验证完成时间 → `approved_at`
>
> 编译通过 ≠ 验证通过。未上板验证前禁止晋升 promote。证据须按`harness/config/baseline-evidence-template.yaml` 填写，并在对应 `harness/config/baseline-status.yaml` 登记，方可晋升为 promoted baseline。

## 禁止行为

**`code/` 允许经 cross-device-apply 或人工编辑（新流程源头）；严禁把 workspace 改动反向归档回 code（sync-workspace-to-code 为 deprecated 历史命令）**。原因：

1. workspace 是编译缓存镜像，改动若未回填 code/，验证结果不代表 code 状态。
2. 反向归档会覆盖 code/ 源头，破坏 code → workspace 单向同步纪律。

## 归档纪律

| 禁止行为 | 原因 | 正确做法 |
|---------|------|---------|
| 未上板验证的 dev 改动 promote 到 main | 晋升未验证代码，违反"验证后晋升"原则 | 走完编译→打包→上板→验证全流程，验证 OK 后经 `sync-modify-to-main-base` 晋升 |
| 调试 workflow 脚本 bug 时运行真实业务命令 | 业务命令有副作用（写 code/改 git 状态），污染 code 源头状态 | 用 `--check-only` / `--dry-run` 验证脚本逻辑，业务命令等流程到位再执行 |
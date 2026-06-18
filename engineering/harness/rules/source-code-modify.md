# 源码改动优先级

## 核心原则

`~/workspace/` 是编译源码树（唯一参与编译），`patchs/` 是单向归档目录。改动必须从源头开始。

## 改动规则

| 目标 | 操作流程 |
|------|---------|
| `patchs/` 下 `kernel/`、`aosp/` 等（对应 workspace 源码） | **必须先改 `~/workspace/` 源码**，验证通过后通过 `sync-code-to-patchs` 命令（`/sync-code-to-patchs`）同步归档 |
| `patchs/others/`（无 workspace 备份的独立程序） | 直接在 `patchs/others/` 中编辑维护 |

> **"验证通过"的定义**（缺一不可）：
> 1. 增量编译成功（`make bootimage/systemimage/vendorimage`）
> 2. 打包镜像成功（`mk_rpi5_full_image.sh`）
> 3. 刷机上板，功能验证 OK
>
> 编译通过 ≠ 验证通过。未上板验证前禁止归档。

## 禁止行为

除 `others/` 外，**`patchs/` 下的目录只允许通过 `sync-code-to-patchs` 命令（`/sync-code-to-patchs`）同步，严禁手动修改**。原因：

1. `patchs/` 不参与编译，直接改此处 workspace 源码不会更新，编译无法生效。
2. 下次执行同步归档时，手动改动会被 workspace 的最新状态覆盖。

## 归档纪律

| 禁止行为 | 原因 | 正确做法 |
|---------|------|---------|
| 未打包/未上板就执行 `sync-code-to-patchs` | 归档未验证代码，违反"验证后归档"原则 | 走完编译→打包→上板→验证全流程 |
| 调试 workflow 脚本 bug 时运行真实业务命令 | 业务命令有副作用（写 patchs/改 git 状态），污染归档状态 | 用 `--check-only` 验证脚本逻辑，业务命令等流程到位再执行 |

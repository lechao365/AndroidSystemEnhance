# 源码改动优先级

## 核心原则

`~/workspace/` 是编译源码树（唯一参与编译），`patchs/` 是单向归档目录。改动必须从源头开始。

## 改动规则

| 目标 | 操作流程 |
|------|---------|
| `patchs/` 下 `kernel/`、`aosp/` 等（对应 workspace 源码） | **必须先改 `~/workspace/` 源码**，验证通过后通过 `sync-code-to-patchs` skill 同步归档 |
| `patchs/others/`（无 workspace 备份的独立程序） | 直接在 `patchs/others/` 中编辑维护 |

## 禁止行为

除 `others/` 外，**`patchs/` 下的目录只允许通过 `sync-code-to-patchs` skill 同步，严禁手动修改**。原因：

1. `patchs/` 不参与编译，直接改此处 workspace 源码不会更新，编译无法生效。
2. 下次执行同步归档时，手动改动会被 workspace 的最新状态覆盖。

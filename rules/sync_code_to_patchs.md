# 同步与归档规则

## 适用范围

本文件约束 `patchs/rpi5/` 目录下的归档操作，适用于所有 agent 和人工操作。

## 一键同步

```bash
bash scripts/sync_code_to_patchs.sh              # 同步归档
bash scripts/sync_code_to_patchs.sh --check-only  # 仅检查，不执行
```

脚本自动完成扫描、归档、验证、陈旧文件检查。详细规则见下方各节。

## 归档规则

### 目录划分

| 目录 | 内容 | 数据来源 |
|------|------|---------|
| `kernel/modified/` | 对上游已有文件的改动（unified diff） | `git diff` → `.diff` 文件 |
| `kernel/new/` | 全部新增的文件/目录（完整文件） | 直接复制 |
| `aosp/modified/` | 对上游已有文件的改动（unified diff） | `git diff` → `.diff` 文件 |
| `aosp/new/` | 全部新增的文件/目录（完整文件） | 直接复制 |
| `others/` | 树莓派5专用程序（测试工具等），独立维护 | 直接在 `others/` 目录中开发维护 |

### modified vs new 判定标准

判定基于 upstream base commit（由 `git merge-base` 自动计算）：

| 场景 | 归档方式 |
|------|---------|
| 文件在 upstream base 中已存在，被修改 | → `modified/`，生成 `.diff` |
| 文件在 upstream base 中不存在（我们新增） | → `new/`，复制完整文件 |
| 目录整体是新增的，其中的文件后续又有修改 | → `new/`，复制最新完整文件 |
| 非 repo 管理目录中的文件 | → `new/`，复制完整文件 |

### 路径映射规则

**patchs 内路径 = 源码相对路径**

| patchs 路径 | 对应源码 |
|-------------|---------|
| `kernel/modified/<相对路径>.diff` | `rpi5-kernel-build/common/<相对路径>` |
| `kernel/new/<相对路径>` | `rpi5-kernel-build/common/<相对路径>` |
| `aosp/modified/<项目路径>/<相对路径>.diff` | `aosp/<项目路径>/<相对路径>` |
| `aosp/new/<相对路径>` | `aosp/<相对路径>` |
| `others/...` | 不对应特定源码树，独立归档 |

### 排除规则

以下文件**不归档**：

| 类型 | 匹配规则 | 原因 |
|------|---------|------|
| 内核编译产物 | `*.o` `*.ko` `*.cmd` `Module.symvers` | 编译中间文件 |
| 二进制镜像 | `Image` `*.dtb` `*.dtbo` | 编译产物 |
| 预构建二进制 | `*.prebuilt` `*.prev` `overlays.prebuilt/` `overlays.prev/` | 上游预构建文件 |
| 构建缓存 | `prebuilts/` `bazel-*` | 工具链/缓存 |
| 构建输出 | `out/` | AOSP 构建输出 |
| 上游未改动文件 | git 无 diff | 不属于本项目 |
| `others/` 目录 | — | 独立维护，直接提交 Git |

## 归档流程说明

以下为 `sync_code_to_patchs.sh` 各步骤的说明，便于理解脚本行为。

1. **Step 0 — 发现非 repo 目录**：对比 repo manifest 和文件系统，自动发现不属于 repo 管理的顶层目录，递归纳入 new/ 扫描。
2. **Step 1 — Kernel 同步**：自动计算 upstream base commit，`--diff-filter=M` 生成 modified `.diff`，`--diff-filter=A` + untracked 文件复制到 new/。
3. **Step 2 — AOSP 同步**：逐 repo 项目计算 upstream base，生成 modified/new；非 repo 目录整体复制到 new/。
4. **Step 3 — 验证完整性**：检查 workspace 每个改动文件在 patchs 中是否已有对应归档。
5. **Step 4 — 陈旧文件检查**：检查 patchs 中是否有 workspace 已不存在的文件。
6. **Step 5 — 更新文件映射表**：根据脚本输出，手动更新 `patchs/rpi5/README.md` 文件映射表（补充"改动要点"描述）。

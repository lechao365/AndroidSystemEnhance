# 同步与归档规则

## 适用范围

本文件约束 `patchs/rpi5/` 目录下的归档操作，适用于所有 agent 和人工操作。

## 归档规则

### 目录划分

| 目录 | 内容 | 数据来源 |
|------|------|---------|
| `kernel/modified/` | 对上游已有文件的改动（unified diff） | `git diff` → `.diff` 文件 |
| `kernel/new/` | 全部新增的文件/目录（完整文件） | 直接复制 |
| `aosp/modified/` | 对上游已有文件的改动（unified diff） | `git diff` 或 `repo diff` → `.diff` 文件 |
| `aosp/new/` | 全部新增的文件/目录（完整文件） | 直接复制 |
| `others/` | 树莓派5专用程序（测试工具等），独立维护，直接提交到 Git | 直接在 `others/` 目录中开发维护 |

### modified vs new 判定标准

| 场景 | 归档方式 |
|------|---------|
| 修改了上游已有的文件（如 `transport.c`、`BoardConfig.mk`） | → `modified/`，生成 `.diff` |
| 新增文件/目录（如 `vendor/lechao/`、`lechao_*.te`） | → `new/`，复制完整文件 |
| 目录整体是新增的，其中的文件后续又有修改 | → `new/`，复制最新完整文件（不拆 diff） |
| vendor/lechao/ 下的内核模块和用户态服务 | → `new/`，整体视为新增 |

### 路径映射规则

**patchs 内路径 = 源码相对路径**

| patchs 路径 | 对应源码 |
|-------------|---------|
| `kernel/modified/drivers/usb/storage/transport.c.diff` | `rpi5-kernel-build/common/drivers/usb/storage/transport.c` |
| `kernel/new/vendor/lechao/LcIod/lciod_usbd.c` | `rpi5-kernel-build/common/vendor/lechao/LcIod/lciod_usbd.c` |
| `aosp/modified/device/brcm/rpi5/device.mk.diff` | `aosp/device/brcm/rpi5/device.mk` |
| `aosp/new/vendor/lechao/services/lechao_lciod/hal/hal_service.cpp` | `aosp/vendor/lechao/services/lechao_lciod/hal/hal_service.cpp` |
| `others/...` | 不对应特定源码树，独立归档 |

### 排除规则

以下内容**不归档**：

| 项目 | 原因 |
|------|------|
| `device/brcm/rpi5-kernel/` 下的二进制文件（Image、dtb、dtbo） | 编译产物 |
| `rpi5-kernel-build/prebuilts/` | 预编译工具链 |
| `rpi5-kernel-build/bazel-*` | 构建缓存 |
| 上游未改动的文件（git 无 diff） | 不属于本项目 |
| `vendor/lechao/LcIod/Module.symvers`、`*.o`、`*.ko`、`*.cmd` | 内核模块编译产物 |
| `others/` 目录 | 独立维护，直接提交 Git，不走归档流程 |

## 归档流程

每次 workspace 有改动后，必须按以下步骤归档：

### 1. 扫描改动

#### Kernel（单 git 仓库）

```bash
cd ~/workspace/rpi5-kernel-build/common
git diff --name-only                              # modified 文件
git ls-files --others --exclude-standard          # new 文件
```

#### AOSP（repo 多仓库）

**Part A: repo 管理的项目（自动扫描）**

```bash
cd ~/workspace/aosp

# 列出所有有改动的项目
repo status 2>/dev/null | grep "^project" | awk '{print $2}' | sed 's|/$||'

# 逐项目检查 modified 和 new
for proj_dir in $(repo status 2>/dev/null | grep "^project" | awk '{print $2}' | sed 's|/$||'); do
    cd ~/workspace/aosp/$proj_dir
    git diff --name-only
    git ls-files --others --exclude-standard
done
```

**Part B: 非 repo 管理的目录（显式清单）**

以下目录不在 AOSP repo 项目中，`repo status` 无法发现。必须手动检查：

| 目录 | 归档位置 |
|------|---------|
| `vendor/lechao/` | `aosp/new/vendor/lechao/` |

```bash
cd ~/workspace/aosp
find vendor/lechao -type f | while read f; do
    target="patchs/rpi5/aosp/new/${f}"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done
```

> **规则**：如果后续在 aosp 中新增了非 repo 目录，必须先在此清单中登记，再执行归档。

### 2. 执行归档

#### 生成 modified/ 的 .diff 文件

```bash
# Kernel
cd ~/workspace/rpi5-kernel-build/common
git diff -- <源码相对路径> > /path/to/AndroidSystemEnhance/patchs/rpi5/kernel/modified/<同路径>.diff

# AOSP（repo 管理的项目用 git diff）
cd ~/workspace/aosp/device/brcm/rpi5
git diff -- <源码相对路径> > /path/to/AndroidSystemEnhance/patchs/rpi5/aosp/modified/device/brcm/rpi5/<同路径>.diff
```

#### 复制 new/ 的完整文件

```bash
cp -r <源码路径> /path/to/AndroidSystemEnhance/patchs/rpi5/<kernel或aosp>/new/<相对路径>
```

#### others/ 目录

`others/` 不走 workspace → patchs 的同步流程。该目录下的工具/程序直接在 `others/` 中开发和维护，通过 Git 提交到本仓库。无需执行归档操作。

### 3. 验证无遗漏

归档完成后，执行以下检查确保 `git status` / `repo status` 中的每个改动都收录：

```bash
# Kernel: modified（排除 vendor/lechao/ 整体，归入 new/）
cd ~/workspace/rpi5-kernel-build/common
PATCH_ROOT=/mnt/d/Code/Github/AndroidSystemEnhance/patchs/rpi5

echo "=== Kernel modified (M) ==="
git diff --name-only | grep -v '^vendor/lechao/' | while read f; do
    target="$PATCH_ROOT/kernel/modified/${f}.diff"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done

echo "=== Kernel new (untracked ??) ==="
git ls-files --others --exclude-standard | grep -v -E '\.(o|ko|cmd|symvers)$' | while read f; do
    target="$PATCH_ROOT/kernel/new/${f}"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done

echo "=== Kernel new (vendor/lechao/ 整体 — tracked 也归入 new/) ==="
find vendor/lechao -type f | grep -v -E '\.(o|ko|cmd|symvers)$|Module\.symvers' | while read f; do
    target="$PATCH_ROOT/kernel/new/${f}"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done

# AOSP: repo 管理的项目，逐项目检查
cd ~/workspace/aosp
PATCH_ROOT=/mnt/d/Code/Github/AndroidSystemEnhance/patchs/rpi5

for proj_dir in $(repo status 2>/dev/null | grep "^project" | awk '{print $2}' | sed 's|/$||'); do
    [ "$proj_dir" = "device/brcm/rpi5-kernel" ] && continue  # 二进制，跳过

    cd ~/workspace/aosp/$proj_dir

    echo "=== AOSP $proj_dir modified (M) ==="
    git diff --name-only | while read f; do
        target="$PATCH_ROOT/aosp/modified/${proj_dir}/${f}.diff"
        [ -f "$target" ] && echo "  OK: ${proj_dir}/${f}" || echo "  MISS: ${proj_dir}/${f}"
    done

    echo "=== AOSP $proj_dir new (??) ==="
    git ls-files --others --exclude-standard | while read f; do
        target="$PATCH_ROOT/aosp/new/${proj_dir}/${f}"
        [ -f "$target" ] && echo "  OK: ${proj_dir}/${f}" || echo "  MISS: ${proj_dir}/${f}"
    done
done

# AOSP: 非 repo 管理的目录（显式清单）
cd ~/workspace/aosp
echo "=== AOSP non-repo directories ==="
find vendor/lechao -type f | while read f; do
    target="$PATCH_ROOT/aosp/new/${f}"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done
```

### 4. 更新文件映射表

归档完成后，在 `patchs/rpi5/README.md` 文件映射表中补充新增/变更的条目。

## 非 repo 管理目录清单

每次新增非 repo 目录时，必须更新以下清单：

| 目录 | 源码路径 | 归档位置 | 登记日期 |
|------|---------|---------|---------|
| vendor/lechao | `~/workspace/aosp/vendor/lechao/` | `aosp/new/vendor/lechao/` | 2026-06-12 |

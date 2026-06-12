# AndroidSystemEnhance 项目约束

## 适用范围

本文件约束 `patchs/rpi5/` 目录下的归档操作，适用于所有 agent 和人工操作。

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

- modified 文件 → 生成 `.diff` 放入 `kernel/modified/` 或 `aosp/modified/`
- new 文件/目录 → 复制完整文件到 `kernel/new/` 或 `aosp/new/`
- others/ → 直接在目录中维护，不走归档流程

### 3. 验证无遗漏

归档完成后，运行 `README.md` 第 4.4 节的同步检查清单，确保 `git status` / `repo status` 中的每个改动都收录。

### 4. 更新 README.md

在 `README.md` 第 6 章文件映射表中补充新增/变更的条目。

## 归档规则

- patchs 内路径 = 源码相对路径
- `modified/` 放 unified diff，`new/` 放完整文件
- `vendor/lechao/` 整体视为新增目录，不拆 diff，统一放 `new/`
- `others/` 直接 Git 提交维护，不走归档流程
- 二进制文件（Image/dtb/dtbo）、prebuilts、bazel 缓存不归档

## 非 repo 管理目录清单

每次新增非 repo 目录时，必须更新以下清单：

| 目录 | 源码路径 | 归档位置 | 登记日期 |
|------|---------|---------|---------|
| vendor/lechao | `~/workspace/aosp/vendor/lechao/` | `aosp/new/vendor/lechao/` | 2026-06-12 |

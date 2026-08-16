# harness 目录

本项目内聚的 harness 能力（迁移自 LcHarness 同源 profile 的精简版），
**不依赖 LcHarness 仓**，去掉了投影 / RID / catalog / registry 等 LcHarness 通用机制。

## 目录结构

```
harness/
├── lib/
│   ├── harness_lib.py      # 精简运行时库（初始化/退出/日志/步骤）
│   └── paths.py            # 路径工具（paths.conf + 环境变量覆盖）
├── config/
│   ├── paths.conf          # 路径配置（PATCHS_DIR / KERNEL_WS / AOSP_WS）
│   ├── git_workspace_util.py   # workspace 扫描排除正则（sync/revert 共享）
│   ├── baseline-status.yaml    # baseline 状态登记表
│   ├── baseline-evidence-template.yaml
│   └── doc-sync-mapping.yaml   # code→文档映射规则
├── skills/
│   ├── lc-harness-sync-code-to-patchs/    # workspace→code 归档
│   ├── lc-harness-revert-code-from-patchs/ # code→workspace 回退（需 promoted baseline）
│   └── lc-harness-sync-patchs-to-doc/      # code→文档同步
├── rules/
│   ├── source-code-modify.md   # SRC-001~004：源码改动优先级/归档纪律
│   ├── cxx-coding-rules.md     # CXX-001~004：C/C++ 编码规范
│   └── plantuml.md             # DOC-002：PlantUML 画图约束
├── reference/
│   └── build-reference.md      # RPI5 编译参考（源自 harness/scripts/mk_rpi5_full_image.sh）
├── scripts/
│   ├── mk_rpi5_full_image.sh   # RPI5 一键编译打包
│   └── apply_preset_bugs.py    # 预设 bug 注入/回退（LE 验证用）
└── log/                        # 运行时产物（plan/verify/构建报告等）
```

## 快速使用

三个工作流命令（opencode 原生命令，见 `.opencode/command/`）：

| 命令 | 用途 |
|------|------|
| `/lc-harness-sync-code-to-patchs` | workspace 已验证改动归档到 `code/rpi5/`（含删除对齐 + manifest 重生成） |
| `/lc-harness-revert-code-from-patchs` | 以 promoted baseline 为真相源，把 workspace 拉回一致（计划→逐条确认→执行→校验） |
| `/lc-harness-sync-patchs-to-doc` | code 变动生成报告，按映射规则同步设计文档 |

也可直接运行脚本：

```bash
python3 harness/skills/lc-harness-sync-code-to-patchs/lc_harness_sync_code_to_patchs.py --check-only
python3 harness/skills/lc-harness-revert-code-from-patchs/lc_harness_revert_code_from_patchs.py --check-only
python3 harness/skills/lc-harness-sync-patchs-to-doc/lc_harness_sync_patchs_to_doc.py --check-only
./harness/scripts/mk_rpi5_full_image.sh -h
```

## 路径配置（harness/config/paths.conf）

| key | 说明 | 覆盖方式 |
|-----|------|---------|
| `PATCHS_DIR` | code 归档根（相对项目根） | — |
| `KERNEL_WS` | kernel workspace 根 | 环境变量 `KERNEL_WS` |
| `AOSP_WS` | AOSP workspace 根 | 环境变量 `AOSP_WS` |

```bash
export KERNEL_WS=~/workspace/rpi5-kernel-build/common
export AOSP_WS=~/workspace/aosp
```

## 控制总纲（状态模型与证据要求）

### code 资产状态模型

patch 资产沿晋升链单向流转：`archive → candidate baseline → promoted baseline`（不可回退）。

| 状态 | 含义 | 最少证据 |
|------|------|---------|
| `archive` | 已归档（sync 后） | baseline_id / source_branch / source_commit / sync_manifest |
| `candidate` | 编译+打包通过 | archive 证据 + build_result / package_result |
| `promoted` | 上板验证通过（可作 revert 真相源） | candidate 证据 + board_verify + approved_by / approved_at |

登记在 `harness/config/baseline-status.yaml`，证据字段模板见 `harness/config/baseline-evidence-template.yaml`。

### 证据要求

1. **build**：增量编译成功（`make bootimage/systemimage/vendorimage`）→ `build_result`
2. **package**：打包镜像成功（`harness/scripts/mk_rpi5_full_image.sh`）→ `package_result`
3. **board verify**：刷机上板，功能验证 OK → `board_verify`
4. **operator**：执行人/批准人 → `approved_by`
5. **timestamp**：验证完成时间 → `approved_at`

> 编译通过 ≠ 验证通过。未上板验证前禁止归档。只有 `promoted` 基线才能作为
> `lc-harness-revert-code-from-patchs` 的恢复真相源（`SRC-004`）。

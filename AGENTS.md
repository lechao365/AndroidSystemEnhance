# AndroidSystemEnhance 项目约束

## 会话语言
所有会话一律使用中文答复（含任务报告、方案说明、代码注释、提交信息等）；代码标识符、路径、命令等按原文保留。

## 源码改动优先级
**改动 `code/`（dev 分支）源码前，必须先加载** [harness/rules/source-code-modify.md](harness/rules/source-code-modify.md)（含验证流程、归档纪律、禁止行为）。
`code/`（dev 分支）是唯一改动源头，`~/workspace/` 是编译缓存镜像（code → workspace 单向同步），改动必须从源头开始。

## Harness 工作流命令

| 命令 | 用途 |
|------|------|
| `/sync-code-to-workspace` | 以 code 仓 dev/main HEAD 为真相源，把 workspace 拉回一致（计划→逐条确认→执行→落盘校验） |
| `/sync-code-to-doc` | code 变动生成报告，按映射规则精准同步设计文档（方案先行，确认后落盘） |
| `/cross-device-emit` | emit 侧生成 CDP 批次（远端强 LLM 分析后产批，输出纯文本，仅 emit 设备） |
| `/cross-device-apply` | 解析 CDP 批次编辑 code/dev，-sv 拉起验证后推送（仅 apply 设备） |
| `/workspace-verify` | code→workspace 同步、增量编译、上板验证并写 data/verify-results 收据（仅 apply 设备） |
| `/git-works-push` | dev 分支 commit + push（收据随批入库，仅 apply 设备） |
| `/publish-main-base` | 一键基线发布：harness 自检 → loop 上板验证 → 修复收敛 → 文档同步 → candidate 登记 → promote 到 main（无法修复则禁止 promote，仅 apply 设备） |
| `/revert-modify-from-main-base` | dev 持续 NG 人工回退到 main 基线并恢复设备（仅 apply 设备） |

harness 能力全部内聚在 `harness/` 目录（不依赖 LcHarness），使用说明见 [harness/README.md](harness/README.md)。

## 路径配置
`harness/config/paths.conf` 是路径单一事实源：`PATCHS_DIR` / `KERNEL_WS` / `AOSP_WS`。
`KERNEL_WS` / `AOSP_WS` 支持环境变量覆盖（`export KERNEL_WS=... AOSP_WS=...`）。
脚本路径引用一律通过 `harness/lib/paths.py` 读取，禁止硬编码工程内路径。

## 并行策略
优先使用子 agent 并行处理独立任务，提升效率并减少主会话上下文污染。

## PlantUML 画图约束
所有 PlantUML 图表编写前，必须参考 [harness/rules/plantuml.md](harness/rules/plantuml.md) 中的规则，防止渲染失败（`DOC-002`）。

## RPI5 环境与开发参考文档
涉及 RPI5 环境搭建、编译、部署、调试、远程访问时，必须先加载 `harness/reference/` 下对应文档（索引见 [harness/reference/README.md](harness/reference/README.md)）：

| 场景 | 必须加载的 reference |
|------|---------------------|
| 涉及 RPI5 AOSP/内核编译、源码获取、ccache、打包 | [build-reference.md](harness/reference/build-reference.md) |
| 涉及 WSL2 / 宿主环境搭建、AOSP 编译前准备 | [env-setup-reference.md](harness/reference/env-setup-reference.md) |
| 涉及镜像写入 SD 卡、首次上电、ADB/串口入口 | [flash-deploy-reference.md](harness/reference/flash-deploy-reference.md) |
| 涉及模块级修改、增量编译、镜像推送、内核替换、回退 | [incremental-dev-reference.md](harness/reference/incremental-dev-reference.md) |
| 涉及日志抓取、串口调试、WSL 映射 USB 设备 | [debug-tools-reference.md](harness/reference/debug-tools-reference.md) |
| 涉及跨网络远程访问 opencode WebUI（Tailscale + Serve） | [remote-access-reference.md](harness/reference/remote-access-reference.md) |

这些文档记录了正确的命令与硬性约束（规则 ID + 违反后果），防止 LLM 使用错误参数或重复踩坑。
人类开发者使用的 VS Code / OpenGrok 源码阅读环境搭建见 [docs/development-tools.md](docs/development-tools.md)。

## C++/内核编码规范
改动 lcview 及内核/用户态协议栈（HAL / Daemon / 内核打点模块）的 C/C++ 源码前，必须先加载 [harness/rules/cxx-coding-rules.md](harness/rules/cxx-coding-rules.md)。
该规则将 P0 检视修复中暴露的 4 类 bug（字节序、资源生命周期、输入防御、故障静默）提炼为 CXX-001~004 硬规则。

## 测试防护
lcview / lciod 模块改动后必须通过单元测试编译验证 **且设备真跑**：
- 编译：`make lechao_lcview_unit_test lechao_lcview_hal_test lechao_lciod_unit_test lechao_lciod_hal_test -j$(nproc)` 无编译错误。
- 设备执行（制度化，覆盖 lcview/lciod 全部 unit_test 与 hal_test）：
  `python3 harness/skills/workspace-verify/ws_upload_tests.py`（从 verify-cases.yaml
  modules 段读 test_targets，nativetest push 到设备运行 gtest 并汇总）。
  仅编译不执行不达标——C++ 单测长期只编译不执行是 nextSeqFor 真 bug 未被发现的
  根因（2026-08-28 本批起强制设备真跑）。
测试源码见 `~/workspace/aosp/vendor/lechao/services/lechao_lcview/tests/` 与 `~/workspace/aosp/vendor/lechao/services/lechao_lciod/tests/`。

## Baseline 使用指引
`/sync-code-to-workspace` 的恢复真相源为 code 仓 dev/main HEAD（`SRC-004` 已放宽，不再强制 promoted baseline；`--auto` 日常同步不受限）。仅当**选择以某个 promoted baseline 为参考**核对证据时，先查 [harness/config/baseline-status.yaml](harness/config/baseline-status.yaml)：
- 新流程（cross-device）：candidate 由 `/publish-main-base --prepare` 依据最新 verify 收据自动登记（登记门禁：收据 result 属 pass 或 skip 且 HEAD^ 等于 verified_commit），人工评审通过后 promote 到 main
- 确认目标 baseline 状态为 `promoted`（证据完整）
- 检查 `build_result` / `package_result` / `board_verify`：PASS/SKIP 均合法，FAIL 须人工复核
- 确认 `approved_by` 和 `approved_at` 已填
- 未完成证据化晋升的 baseline 不得宣称为基线

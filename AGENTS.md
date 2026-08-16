# AndroidSystemEnhance 项目约束

## 源码改动优先级
**改动 `~/workspace/` 下任何源码前，必须先加载** [harness/rules/source-code-modify.md](harness/rules/source-code-modify.md)（含验证流程、归档纪律、禁止行为）。
`~/workspace/` 是编译源码树（唯一参与编译），`code/` 是单向归档目录，改动必须从源头开始。

## Harness 工作流命令

| 命令 | 用途 |
|------|------|
| `/lc-harness-sync-code-to-patchs` | workspace 已验证改动归档到 `code/rpi5/`（含删除对齐 + manifest 重生成 + README 映射表更新） |
| `/lc-harness-revert-code-from-patchs` | 以 promoted baseline 为真相源，把 workspace 拉回一致（计划→逐条确认→执行→落盘校验） |
| `/lc-harness-sync-patchs-to-doc` | code 变动生成报告，按映射规则精准同步设计文档（方案先行，确认后落盘） |

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
lcview 模块改动后必须通过单元测试编译验证：`make lechao_lcview_unit_test lechao_lcview_hal_test -j$(nproc)` 无编译错误。
测试源码见 `~/workspace/aosp/vendor/lechao/services/lechao_lcview/tests/`。

## 文件删除规则
1. **任何文件删除操作（无论是否被 git 跟踪）都必须逐个向用户确认，禁止自行删除。**
   - 包括但不限于：临时产物（evidence_bundle.json / summary.txt 等）、测试输出、中间文件、日志、缓存。
   - 操作前必须列出待删文件清单，等用户显式确认（`y` / 同意 / 删吧）后才执行。
2. **`.gitignore` 排除的文件无需考虑删除问题**——它们不会被提交到 git server，不影响仓库状态。
   - 判断依据：`git check-ignore <file>` 返回 0（被忽略）则无需确认。
3. **禁止以"清理"为由批量删除**——即使看似无用，也必须逐个确认。

## Baseline 使用指引
在执行 `/lc-harness-revert-code-from-patchs` 回退操作前，必须先查 [harness/config/baseline-status.yaml](harness/config/baseline-status.yaml)：
- 确认目标 baseline 状态为 `promoted`（证据完整）
- 检查 `build_result` / `package_result` / `board_verify` 均为 PASS
- 确认 `approved_by` 和 `approved_at` 已填
- 未完成证据化晋升的 baseline 不得作为恢复真相源

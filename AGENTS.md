# AndroidSystemEnhance 项目约束

## 源码改动优先级
**改动 `~/workspace/` 下任何源码前，必须先加载** [/mnt/d/Code/Github/LcHarness/core/rules/source-code-modify.md](/mnt/d/Code/Github/LcHarness/core/rules/source-code-modify.md)（含验证流程、归档纪律、禁止行为）。
`~/workspace/` 是编译源码树（唯一参与编译），`patchs/` 是单向归档目录，改动必须从源头开始。

## 并行策略
优先使用子 agent 并行处理独立任务，提升效率并减少主会话上下文污染。
具体策略详见 [/mnt/d/Code/Github/LcHarness/core/rules/parallel-strategy.md](/mnt/d/Code/Github/LcHarness/core/rules/parallel-strategy.md)。

## PlantUML 画图约束
所有 PlantUML 图表编写前，必须参考 [/mnt/d/Code/Github/LcHarness/core/rules/plantuml.md](/mnt/d/Code/Github/LcHarness/core/rules/plantuml.md) 中的规则，防止渲染失败。

## 脚本维测规则（observability）
改动 `engineering/` 下任何 bash 脚本（含 workflows/、scripts/、未来 loop/ 等）前，必须先加载 [/mnt/d/Code/Github/LcHarness/core/rules/script-observability.md](/mnt/d/Code/Github/LcHarness/core/rules/script-observability.md)。
该规则强制要求：source 公共库、接入文件日志、结构化 step、错误现场捕获、统一退出码、中间产物归档。`engineering/output/log/` 为本地维测产物，不归档。

## 文档归档路径
所有设计规格和实施计划保存到 `docs/specs/` 和 `docs/plans/`，禁止使用 `docs/superpowers/`。
详细规则详见 [/mnt/d/Code/Github/LcHarness/core/rules/doc-paths.md](/mnt/d/Code/Github/LcHarness/core/rules/doc-paths.md)。

## 文档索引一致性
改动 `engineering/harness/` 下任何文件（脚本、规则、配置、文档、lib）后，必须检查相关 README.md 是否需要同步更新（新增/删除/重命名文件时尤其关键）。
详细检查清单见 [/mnt/d/Code/Github/LcHarness/README.md](/mnt/d/Code/Github/LcHarness/README.md) 的「README 同步」章节。

## 路径管理
`engineering/` 下所有脚本（shell / python / bat）禁止硬编码工程内路径，统一通过 `/mnt/d/Code/Github/LcHarness/core/config/harness-paths.conf`（单一事实源）+ 三方路径工具获取。
改动任何脚本的路径引用前，必须先加载 [/mnt/d/Code/Github/LcHarness/core/rules/path-management.md](/mnt/d/Code/Github/LcHarness/core/rules/path-management.md)（PATH-001）。
目录调整时仅修改 `paths.conf`，无需改动脚本。

## RPI5 编译参考
涉及 RPI5 AOSP/内核编译时，必须先加载 [engineering/harness/reference/build-reference.md](engineering/harness/reference/build-reference.md)。
该规则记录了本项目正确的编译命令与约束，防止 LLM 使用错误参数。

## C++/内核编码规范
改动 lcview 及内核/用户态协议栈（HAL / Daemon / 内核打点模块）的 C/C++ 源码前，必须先加载 [/mnt/d/Code/Github/LcHarness/core/rules/cxx-coding-rules.md](/mnt/d/Code/Github/LcHarness/core/rules/cxx-coding-rules.md)。
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

## Manifest 准入查询
进入任何任务前，先查询 `/mnt/d/Code/Github/LcHarness/core/rules/manifest.yaml` 确认：
- 当前路径匹配的 context
- 对应 access 级别（direct_edit / require_workflow / require_plan / require_confirmation / require_evidence）
- 必经 workflow（如有）
- 是否需 plan / confirmation / evidence

也可通过 `bash /mnt/d/Code/Github/LcHarness/core/scripts/check_access.sh --path <path> --category <category>` 快速查询。

## Baseline 使用指引
在执行 `lc-revert-code-from-patchs` 回退操作前，必须先查 `/mnt/d/Code/Github/LcHarness/core/config/baseline-status.yaml`：
- 确认目标 baseline 状态为 `promoted`（证据完整）
- 检查 `build_result` / `package_result` / `board_verify` 均为 PASS
- 确认 `approved_by` 和 `approved_at` 已填
- 未完成证据化晋升的 baseline 不得作为恢复真相源

## LcHarness 控制面快捷命令

```bash
alias lc-attach='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-attach.sh'
alias lc-status='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-status.sh'
alias lc-detach='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-detach.sh'
alias lc-validate='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-validate.sh'
alias lc-reconcile='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-reconcile.sh'
```

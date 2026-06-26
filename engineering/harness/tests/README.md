# Tests

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：harness 层测试集——observability 公共库单元测试 + workflow 脚本契约测试（含跨边界测试 loop 脚本的清理契约）。
- **职责边界**：只测 harness 公共库与 workflow 脚本的契约正确性；不测 loop 框架业务逻辑（loop 自身测试在 `../../loop/**/tests/`）。
- **上下游依赖**：依赖 `../lib/`（harness_path_util / harness_bootstrap）、被测 workflow 脚本；跨边界引用 loop 脚本时仅限被测对象本身，不引入 loop 实现依赖。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 测试文件清单与职责 | 选择测试时 |
| [使用方式](#使用方式) | 测试运行命令 | 执行测试时 |
| [关联资源](#关联资源) | 被测对象与规则链接 | 深入理解时 |

## 目录说明

| 文件/目录 | 作用 | 关键入口 |
|----------|------|---------|
| [`test_harness_observability.sh`](./test_harness_observability.sh) | observability 公共库单元测试（`step_begin`/`step_end`/`harness_init` 等） | `bash test_harness_observability.sh` |
| [`test_le_runs_cleanup.sh`](./test_le_runs_cleanup.sh) | `le_runs_cleanup.sh` 清理契约测试（跨边界测试 loop 脚本，验证 harness observability 依赖） | `bash test_le_runs_cleanup.sh` |
| [`test_revert_code_from_patchs.sh`](./test_revert_code_from_patchs.sh) | `revert_code_from_patchs.sh` workflow 契约测试（non-repo-extra / upstream-missing / verify-matrix 三场景） | `bash test_revert_code_from_patchs.sh` |
| [`test_sync_code_to_patchs.sh`](./test_sync_code_to_patchs.sh) | `sync_code_to_patchs.sh` workflow 契约测试 | `bash test_sync_code_to_patchs.sh` |
| [`fixtures/`](./fixtures) | 测试夹具目录（observability/basic、lc-revert-code-from-patchs/*、lc-sync-code-to-patchs/*） | 各测试脚本通过 `FIXTURE*` 变量引用 |

## 使用方式

```bash
# 运行单个测试
bash engineering/harness/tests/test_harness_observability.sh

# 运行全部（依次执行）
for t in engineering/harness/tests/test_*.sh; do bash "$t" || break; done
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 被测公共库 | `../lib/shell/harness_bootstrap.sh` | observability 公共库 |
| 被测公共库 | `../lib/shell/harness_path_util.sh` | 路径工具 |
| 被测 workflow | `../workflows/lc-sync-code-to-patchs/`、`../workflows/lc-revert-code-from-patchs/` | workflow 脚本 |
| 跨边界被测 | `../../loop/scripts/le_runs_cleanup.sh` | loop 清理脚本（依赖 harness observability） |
| 关联规则 | `../rules/script-observability.md`（OBS-001/002） | observability 契约定义 |

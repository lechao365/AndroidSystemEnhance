# Lib

bash 公共库——为 `engineering/` 下所有脚本（workflows/、scripts/）提供统一的日志、结构化 step、错误捕获、产物归档能力。

## 文件说明

- [`harness_observability.sh`](./harness_observability.sh) — 脚本维测公共库，source 后提供 `harness_init` / `log_info` / `log_warn` / `log_error` / `step_begin` / `step_end` / `on_err` / `artifact_register` / `harness_exit` 等函数。规则详见 [rules/script-observability.md](../rules/script-observability.md)。

## 使用约定

- 所有 harness 脚本**必须** source 本库（见 `script-observability.md` 强制要求）。
- 脚本通过 `REPO_ROOT` 锚点查找路径定位本库，不硬编码绝对路径。
- 日志产物输出到 `harness/log/<script-name>/`，由本库自动创建。

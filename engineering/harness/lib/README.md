# Lib

bash 公共库——为 `engineering/` 下所有脚本（workflows/、scripts/）提供统一的入口定位、日志、结构化 step、错误捕获、产物归档能力。

## 文件说明

- [`harness_bootstrap.sh`](./harness_bootstrap.sh) — bootstrap 入口库，负责统一定位 `REPO_ROOT`、加载 `harness_observability.sh`、为业务脚本提供单一 source 入口。所有 harness 脚本必须优先 source 本文件，禁止各自重复实现仓库根定位逻辑。
- [`harness_observability.sh`](./harness_observability.sh) — 维测公共库，提供 `harness_init` / `log_info` / `log_warn` / `log_error` / `step_begin` / `step_end` / `on_err` / `artifact_register` / `harness_exit` 等函数；通常由 `harness_bootstrap.sh` 间接加载，业务脚本不应自行绕过 bootstrap 直接依赖内部实现。

## 使用约定

- 所有 harness 脚本**必须**先 source `harness_bootstrap.sh`（见 `script-observability.md` 强制要求）。
- 业务脚本只依赖公开 API，不直接依赖 `_H_*` / `_h_*` 私有变量或函数。
- 日志产物输出到 `engineering/output/log/<script-name>/`，由公共库自动创建与轮转。

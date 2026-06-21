# engineering/output/

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位
- **是什么**：工程维测与运行产物统一目录（所有脚本运行时产生的日志和产物均落入此目录）
- **职责边界**：产物承载层，不承载实现逻辑（脚本在 harness/loop）
- **上下游依赖**：被 `harness_observability.sh`（log/）、`start_rp5_serial_host.bat`（host-log/）、`le.sh`（runs/）写入；**本地维测产物，不归档**（AGENTS.md 明确）

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [大纲](#大纲) | 本 README 章节索引 | 判断需要读哪些段 |
| [目录说明](#目录说明) | 子目录清单与 .gitkeep 机制 | 了解结构时 |
| [使用方式](#使用方式) | 无可执行入口，runs/ 自动清理 | 实际使用时 |
| [关联资源](#关联资源) | workflow、设计文档链接 | 深入理解时 |

## 目录说明

| 子目录 | 职责 | 被谁写入 |
|-------|------|---------|
| `host-log/` | rp5-serial Windows Host 产物：transcript + host 进程日志 | `start_rp5_serial_host.bat` |
| `log/` | WSL2 端 harness 脚本统一日志，每脚本独立子目录 `<name>/<ts>.log` + `latest.log`，自动轮转保留最近 3 份 | `harness_observability.sh` |
| `runs/` | LE 框架运行产物，按时间戳 `<ts>-<scenario>/`，含 `baseline/report.json` + `summary.txt` | `le.sh` |

> 各子目录通过 `.gitkeep` 占位纳入版本控制，运行产物本身 gitignore 不提交。

## 使用方式

本目录无可执行入口，仅作为产物承载层。

### runs/ 自动清理

`le.sh` 每次运行结束自动调 [`le_runs_cleanup.sh`](../loop/scripts/le_runs_cleanup.sh)，保留最新 N 份（默认 20，`LE_RUNS_KEEP` 或 `--keep N` 覆盖），仅清子目录，散文件保留。

手动：`bash engineering/loop/scripts/le_runs_cleanup.sh --keep 20 --dry-run`。退出码：`0`=已清理或目录不存在（视为无操作成功）；`1`=部分删除失败；`3`=参数错误；`4`=无操作（含 `--dry-run`、未超份数）。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `../harness/workflows/` | 脚本运行产物落入 log/ |
| 关联 workflow | `../loop/workflows/lcview-adb-run/` | 产物落入 runs/ |
| 设计文档 | `docs/specs/2026-06-21-engineering-doc-refactor-design.md` | output 定位：本地维测，不归档 |

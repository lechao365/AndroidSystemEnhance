# Loop Scripts

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位
- **是什么**：loop engineering 专属脚本入口（CLI wrapper、产物清理、串口辅助、Windows host 启动器）
- **职责边界**：允许依赖 `harness/lib/`；禁止把 loop 专属脚本放回 `harness/scripts/`
- **上下游依赖**：被 `le.sh` / `start_rp5_serial_host.bat` 调用，依赖 `harness/lib/`（bootstrap / path / observability）

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [大纲](#大纲) | 本 README 章节索引 | 判断需要读哪些段 |
| [目录说明](#目录说明) | 脚本清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 快速开始、入口清单、退出码 | 实际使用时 🔖 |
| [关联资源](#关联资源) | 设计文档、规则、workflow 链接 | 深入理解时 |

## 目录说明

| 脚本 | 作用 | 调用方式 |
|------|------|---------|
| [`le.sh`](./le.sh) | Loop Engineering CLI wrapper，支持 `run` / `runtime`（新主入口） | `/le` 或 `bash engineering/loop/scripts/le.sh runtime init/run/resume/status/explain` |
| [`le_runs_cleanup.sh`](./le_runs_cleanup.sh) | runs/ 产物清理，保留最新 N 份 | `--keep N --dry-run` |
| [`rp5_serial_helper.py`](./rp5_serial_helper.py) | 供 loop host case / workflow 使用的串口辅助工具（如 adb endpoint 发现） | 被 workflow import，非 CLI 直接入口 |
| [`start_rp5_serial_host.bat`](./start_rp5_serial_host.bat) | Windows 前台启动 rp5-serial Host，独占物理串口 | CMD 中 `engineering\loop\scripts\start_rp5_serial_host.bat COM5 115200 9700` |

## 使用方式

### 快速开始
```bash
# fixture 模式（离线回放）
bash engineering/loop/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture <jsonl路径> \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases --artifacts-dir <输出目录>
# live 模式（需先启动 Windows Host，改用 --host/--port 替换 --fixture）
bash engineering/loop/scripts/le.sh run --suite ... --host 127.0.0.1 --port 9700 ...
```
> `le.sh` 通过 `harness_bootstrap.sh` 自动加载路径配置和维测框架，无需手动设置 `PYTHONPATH`。

### 入口清单
| 入口 | 作用 | 调用方式 |
|------|------|---------|
| `le.sh runtime` | **Runtime CLI**（新主入口）：`init/run/resume/status/explain` | `le.sh runtime init --target ... --suite ... --artifacts-dir ...` |
| `le.sh run` | LE 验证 CLI（fixture/live） | `le.sh run --suite ... --fixture/--host ...` |
| `le_runs_cleanup.sh` | runs/ 产物清理 | `le_runs_cleanup.sh --keep 20 --dry-run` |
| `start_rp5_serial_host.bat` | Windows Host 启动 | CMD: `start_rp5_serial_host.bat COM5 115200 9700` |
| `rp5_serial_helper.py` | 串口辅助（内部调用） | 被 workflow import，非直接 CLI |

### 退出码
`le_runs_cleanup.sh`：`0`=已清理；`1`=部分删除失败；`3`=参数错误；`4`=无操作（含 `--dry-run`、未超份数、目录不存在）。

### Windows .bat 格式规则
Windows `.bat` 文件的格式要求（CRLF / 纯 ASCII / CMD 运行 / 修改后验证）见 [`engineering/harness/scripts/README.md`](../../harness/scripts/README.md)（SSOT，D3 决策），本 README 不重复。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | loop 架构 |
| 关联规则 | `../../harness/rules/path-management.md`（PATH-001） | `.bat` 路径工具约束 |
| 关联 runtime | `../controller/README.md` | `le.sh runtime` 由 runtime engine 驱动 |

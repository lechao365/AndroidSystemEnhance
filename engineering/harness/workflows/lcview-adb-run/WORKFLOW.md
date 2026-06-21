# lcview-adb-run Workflow

## 目标

提供单入口 workflow：
1. 用 serial profile 跑 `system/network-adbd-success.yaml`（bootstrap）
2. 提取 adb endpoint（从 serial helper 或参数直传）
3. 用 adb profile 跑 `features/lcview/end_to_end.yaml`（feature run）
4. adb run 失败时补采 serial context（fallback）
5. 汇总 bootstrap / feature artifacts 与 failure code

## 输入参数

- `--serial-host`（默认 127.0.0.1）
- `--serial-port`（默认 9700）
- `--adb-endpoint`（可选；为空时自动从 serial helper 发现）
- `--artifacts-dir`（默认 `engineering/output/runs/lcview-adb-run`）
- `--serial-profile`（默认 rp5/default.json）
- `--adb-profile`（默认 rp5/adb.json）

## 失败分型

| failure code | 含义 |
|---|---|
| `BOOTSTRAP_FAIL` | bootstrap 阶段失败（串口未通 / WiFi 未连 / adbd 未启动） |
| `ADB_CONNECT_FAIL` | adb endpoint 缺失或 adb connect 失败 |
| `ADB_EXEC_FAIL` | adb suite 运行中命令执行异常 |
| `LCVIEW_PREREQ_FAIL` | lcview 前提不满足（服务/ schema / 目录） |
| `LCVIEW_TRIGGER_FAIL` | trigger 动作执行失败 |
| `LCVIEW_PIPELINE_FAIL` | jsonl 未生成或内容为空 |
| `LCVIEW_EVIDENCE_FAIL` | 关键 evidence pull 失败 |

## 脚本入口

`run_lcview_adb_suite.sh`
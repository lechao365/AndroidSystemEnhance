# Loop Engineering

AI 驱动的设备验收闭环：用例驱动 + EvidenceBundle + opencode AI 分析修复。

## 架构

```
opencode (AI Driver)
    ↓ le run
LE 框架 (loop_core)
    ├── case_loader       YAML 用例加载（include/requires）
    ├── assertion_engine  确定性断言（6 种类型）
    ├── executor          用例执行 + collector 触发
    ├── runner            通用 LoopRunner（场景无关）
    └── evidence          EvidenceBundle JSON 输出
    ↓ transport
connection (rp5-serial provider)
```

## 目录结构

```
engineering/loop/
├── core/python/loop_core/       LE 框架（通用层）
├── cases/                       声明式用例（YAML）
│   ├── common/                    公共 suite（含 shell + 诊断 collector 库）
│   ├── modules/                   模块级用例（第二步）
│   └── system/                    系统级用例
├── templates/                   AI 生成约束模板
│   └── case-template.md
└── connection/                  连接层（provider）
    ├── profiles/devices/rp5/
    └── providers/rp5-serial/

> CLI 入口脚本已移至 `engineering/harness/scripts/le.sh`  
> Windows Host 启动脚本已移至 `engineering/harness/scripts/start_rp5_serial_host.bat`
```

### 公共 suite 与诊断 collector 库

`cases/common/shell.yaml`（suite: `common.shell`）承载两类可复用资产：

| 资产 | FQN | 说明 |
|------|-----|------|
| shell 可达性原子用例 | `common.shell.shell_reachable` | 作为系统用例的 `requires` 前置 |
| boot 诊断 collector | `common.shell.boot_log` | dmesg |
| init 诊断 collector | `common.shell.init_log` | getprop init.svc.* / logcat -b system |
| 崩溃诊断 collector | `common.shell.crash_dump` | logcat -b crash / tombstones |
| 串口上下文 collector | `common.shell.serial_recent` | transcript path + serial snippet（mode: serial_context） |

业务 suite 通过 `include` 即可获得上述全部资产：

```yaml
suite: my.module
include:
  - common/shell    # 注入 shell_reachable 用例 + 三个公共 collector

cases:
  - id: my_check
    command: "..."
    assert: {type: contains, value: "..."}
    requires: [shell_reachable]                 # 短名 → common.shell.shell_reachable
    on_fail:
      collectors: [crash_dump, init_log]        # 短名 → common.shell.<name>
```

> 短名解析规则见 `core/python/loop_core/case_loader.py:_resolve_case_links`：
> 本地命名空间优先 → 显式 FQN → 全局唯一短名回退。

> include 路径（如 `common/shell`）由 `--case-dirs` 解析。loader 会在每个
> case_dir 下查找 `<name>.yaml`，因此 `--case-dirs` 必须包含 `cases/` 根目录
> （见下方快速开始示例）。

## 快速开始

### fixture 模式（离线回放）

```bash
bash engineering/harness/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture <jsonl路径> \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir <输出目录>
```

### live 模式

```bash
# 先启动 Windows Host（COM5）
# 然后在 WSL2 执行：
bash engineering/harness/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --host 127.0.0.1 --port 9700 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir <输出目录>
```

## 添加新场景

只需写 1 个 YAML 用例文件，零 Python 代码：

```bash
# 1. 参照模板编写用例
# 参考 engineering/loop/templates/case-template.md

# 2. 创建用例文件
# engineering/loop/cases/system/<your-scenario>.yaml

# 3. 执行（case-dirs 指向 cases 根目录，保证 include: [common/shell] 可解析）
bash engineering/harness/scripts/le.sh run --suite <path> \
  --case-dirs engineering/loop/cases ...
```

## 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib
```

## EvidenceBundle 串口上下文

`evidence_bundle.json` 包含 `serial_context` 字段，承载串口第一现场证据：

| 字段 | 说明 |
|------|------|
| `transcript_path` | host 持续落盘的串口 transcript 文件路径 |
| `serial_snippet` | 最近 N 行（≤40）串口关键片段 |
| `reboot_cycles` | 基于 `reboot_markers` 估算的最近重启周期数 |
| `recent_line_count` | host 当前环形缓冲中的行数 |

`summary.txt` 同步渲染上述内容，方便人工快速浏览。

## 串口 transcript

rp5-serial host 持续将串口正文写入 `transcript_path`（默认 `output/host-log/rp5-serial-transcript.log`），
每行带 ISO 时间戳。`serial_recent` collector 通过 `mode: serial_context` 直接消费 host 上下文，
无需 shell 可达即可获取串口根证据（transcript 路径 + 最近片段 + restart 周期）。

## 设计文档

- `docs/specs/2026-06-19-loop-engineering-v2-design.md`（v2 架构，权威来源）
- `docs/specs/2026-06-20-loop-zygote-restart-serial-observability-design.md`（串口观测补强设计）
- `docs/specs/2026-06-19-loop-core-extraction-design.md`（core 抽取）
- `docs/specs/2026-06-19-loop-engineering-design.md`（v1 原始设计，历史归档）

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
├── bin/le.sh                    统一 CLI 入口
├── core/python/loop_core/       LE 框架（通用层）
├── cases/                       声明式用例（YAML）
│   ├── common/                    公共原子用例
│   ├── modules/                   模块级用例（第二步）
│   └── system/                    系统级用例
├── templates/                   AI 生成约束模板
│   └── case-template.md
├── connection/                  连接层（provider）
│   ├── profiles/devices/rp5/
│   └── providers/rp5-serial/
└── scripts/                     辅助脚本
    └── start_rp5_serial_host.bat
```

## 快速开始

### fixture 模式（离线回放）

```bash
bash engineering/loop/bin/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture <jsonl路径> \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases/common,engineering/loop/cases/system \
  --artifacts-dir <输出目录>
```

### live 模式

```bash
# 先启动 Windows Host（COM5）
# 然后在 WSL2 执行：
bash engineering/loop/bin/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --host 127.0.0.1 --port 9700 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases/common,engineering/loop/cases/system \
  --artifacts-dir <输出目录>
```

## 添加新场景

只需写 1 个 YAML 用例文件，零 Python 代码：

```bash
# 1. 参照模板编写用例
# 参考 engineering/loop/templates/case-template.md

# 2. 创建用例文件
# engineering/loop/cases/system/<your-scenario>.yaml

# 3. 执行
bash engineering/loop/bin/le.sh run --suite <path> ...
```

## 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib
```

## 设计文档

- `docs/specs/2026-06-19-loop-engineering-v2-design.md`（v2 架构，权威来源）
- `docs/specs/2026-06-19-loop-core-extraction-design.md`（core 抽取）
- `docs/specs/2026-06-19-loop-engineering-design.md`（v1 原始设计，历史归档）

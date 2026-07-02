# Loop Engineering

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：AI 驱动的设备验收闭环——用例驱动 + EvidenceBundle + opencode AI 分析修复
- **职责边界**：承载 loop engineering 专属能力（cases / connection / core / scripts / controller / workflows / contracts）；不承载公共 harness 基础设施（在 `../harness/`）
- **未来映射**：在独立 `LcHarness` 架构中，`loop engineering` 作为 solution pack 存在，不进入 core。
- **上下游依赖**：依赖 `engineering/harness/`（规则/路径/observability）；被 `.opencode/commands/le.md` 通过 `@WORKFLOW.md` 消费

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 子目录/文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 快速开始、测试、添加场景 | 实际跑 le run 时 |
| [关联资源](#关联资源) | 设计文档、规则、workflow、配置链接 | 深入理解时 |
| 流程细节（架构/场景/诊断约束） | 见 `WORKFLOW.md` | 深入理解流程时 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口/被谁引用 |
|------------|------|------------------|
| `core/python/loop_core/` | LE 框架通用层 | 详见 WORKFLOW.md「core 模块清单」 |
| `cases/` | 声明式用例（YAML），含 common/ features/ system/ | `cases/common/shell.yaml` 公共 suite + 诊断 collector（详见 WORKFLOW.md） |
| `connection/` | 连接层（provider/profiles/protocol） | 详见 `connection/README.md` |
| `scripts/` | CLI 入口 le.sh + host 启动脚本 | 详见 `scripts/README.md` |
| `templates/case-template.md` | AI 用例生成约束模板 |
| `workflows/` | loop 专属 workflow 容器（当前为空，待未来 phase plan 入驻） | 详见 `workflows/README.md` |
| `controller/` | loop 控制面与 runtime 编排中心（`loop_controller` Python 包：状态图 runtime engine + guard + checkpoint + stages + patch） | 详见 `controller/README.md` |
| `contracts/` | loop 契约层（`loop_contracts` Python 包：LoopSession / RuntimeState / CheckpointRecord / FailureCode） | 详见 `contracts/README.md` |
| `config/` | loop 配置文件（`target-paths.yaml` 等补丁白名单） | 被 `controller/patch_guard.py` 读取 |
| `deploy/` | loop 部署层（`loop_deploy` Python 包：compile / deploy / rollback / image_verify） | 被 runtime nodes 与 `le deploy` 调用 |
| `WORKFLOW.md` | **流程细节单一事实源**（架构拓扑 / core 模块 / 断言类型 / run_on / 场景细节 / serial_context / 诊断约束） | 被 `/le` 注入 |

> 子目录自身的细节见其 `README.md`，本表只给一句话索引。流程级细节见 WORKFLOW.md。

## 使用方式

### 快速开始

**fixture 模式（离线回放）**：

```bash
bash engineering/loop/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture <jsonl路径> \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir <输出目录>
```

**live 模式**：

```bash
# 先启动 Windows Host（COM5），然后在 WSL2 执行：
bash engineering/loop/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --host 127.0.0.1 --port 9700 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir <输出目录>
```

### 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib
```

### 添加新场景

参照 `templates/case-template.md` 写 YAML，零 Python，详见 WORKFLOW.md「扩展新场景」。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-v2-design.md` | v2 架构，权威 |
| 设计文档 | `docs/specs/2026-06-20-loop-zygote-restart-serial-observability-design.md` | 串口观测 |
| 设计文档 | `docs/specs/2026-06-20-le-zygote-diagnosis-and-patch-draft-design.md` | 诊断与补丁草案 |
| 设计文档 | `docs/specs/2026-06-19-loop-core-extraction-design.md` | core 抽取 |
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | v1 历史归档 |
| 关联规则 | `../harness/rules/script-observability.md` | 改 loop 下 bash 脚本时 |
| 关联规则 | `../harness/rules/path-management.md` | 路径引用 |
| 关联配置 | `../harness/config/harness-paths.conf` | LOOP_* 路径 KEY |

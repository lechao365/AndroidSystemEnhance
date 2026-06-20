# Loop 串口观测补强与 Zygote 重启定位能力设计

> **日期**：2026-06-20
> **状态**：已确认
> **范围**：在 Loop Engineering v2 基线之上，补齐"持续串口 transcript + shell 可达后的 zygote 重启根因证据增强 + 结构化归档"，使 LE 可在 shell 可达场景下稳定定位 zygote 反复重启问题，同时为 shell 不可达场景预置 transcript 底座。
> **前序**：基于 `docs/specs/2026-06-19-loop-engineering-v2-design.md` 与当前已落地实现。

---

## 1. 背景

### 1.1 当前能力

LE v2 的 `boot-success` suite 可以判断 `zygote` 当前状态：

- `shell_reachable`（`engineering/loop/cases/common/shell.yaml:11`）：检查 prompt 是否可见
- `zygote_running`（`engineering/loop/cases/system/boot-success.yaml:29`）：`getprop init.svc.zygote` 含 `running`
- `zygote_running` 失败时触发 `crash_dump` + `init_log` collector（`engineering/loop/cases/system/boot-success.yaml:37`）

当前能回答："此刻 zygote 是 running 吗？"  
但不能回答："zygote 为什么反复重启？前面发生了什么？"

### 1.2 核心缺口

1. **串口流失线**：rp5-serial host 只保留 500 行内存环形缓冲（`engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py:19`），没有持久 transcript，也无法回溯
2. **伪时间戳**：live transport 给每行造 `i * 0.01` 相对时间（`engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py:23`），无法判断重启频率和间隔
3. **循环丢失**：profile 已定义 `reboot_markers`（`engineering/loop/connection/profiles/devices/rp5/default.json:5`），但 `LoopRunner -> CaseExecutor` 主链路未使用
4. **证据不关联**：`CollectorResult.artifact_paths` 仍是占位字段（`engineering/loop/core/python/loop_core/models.py:79`），transcript 路径未写入 bundle
5. **最坏场景失明**：`shell_reachable` fail 时没有 collector，依赖 fail 时全链路 skip

### 1.3 本次设计范围

本次以 **最小可用定位链路** 为目标，不扩成"通用 observability 平台"：

- **中心**：shell 可达后在 EvidenceBundle 中提供完整 zygote 重启定位证据
- **底座**：host 持续 transcript 落盘，解决串口主体证据缺失
- **扩展性**：上述可以很自然地用于服务重启、panic、启动挂死等其他诊断场景

---

## 2. 目标

1. **串口按需持久落盘**：host 记录 `transcript_path`，同一行同时含文本和真实 ISO 时间戳
2. **live transport 按需输出**：从 transcript 获取 `serial_snippet`、`transcript_path`，算 `reboot_cycles`
3. **EvidenceBundle 扩展**：增加 `serial_context` 字段，承载 transcript 引用、最近片段、重启轮回摘要
4. **shell 可达后增强 zygote 重启诊断**：在 `boot-success` 中加强 `crash_dump` / `init_log` 等 collector 以及和 `serial_context` 的关联
5. **制品归档**：一次性产生 `evidence_bundle.json`、`summary.txt`，AI/人工可以直接从中看到关键 restart 证据

---

## 3. 非目标

1. **不**实现复杂根因自动分类或规则引擎
2. **不**做 ADB / 多设备 / 服务自恢复等体系重构
3. **不**在同一轮做 shell 不可达场景的自动诊断（但保留 transcript 底座）
4. **不**修改 harness 核心机制
5. **不**修改 `docs/` 中已归档的历史 spec/plan
6. **不**删除 `workflows/`（v2 中已完成）

---

## 4. 已确认决策

| # | 议题 | 决策 | 说明 |
|---|------|------|------|
| 1 | 本次范围 | 最小可用定位链路 | 不扩成通用 observability 平台 |
| 2 | 优先场景 | shell 可达后的根因定位完整性 | 重点增强 `logcat/tombstone/getprop/init` 信息关联 |
| 3 | transcript 落盘 | 必做 | 作为基础能力纳入，避免后面返工 |
| 4 | 时间戳来源 | host 侧分配真实 ISO 时间 | 由 `serial_runtime.py` 进程 `datetime.now().astimezone()` 生成 |
| 5 | transcript 命名 | `rp5-serial-transcript.log` | 放在 `output/host-log/` 目录 |
| 6 | recent buffer 上限 | 从 500 扩大到 2000 | `MAX_LINE_BUFFER = 2000` |
| 7 | EvidenceBundle 新字段 | `serial_context` | 含 `transcript_path`, `serial_snippet`, `reboot_cycles`, `recent_line_count` |
| 8 | collector 新增模式 | `mode: serial_context` | 不用 shell exec 命令，直接拿 transport 暴露的 runtime context |
| 9 | CLI 入口 | `le.sh` 保持不变 | 新字段由 loop_core 和 provider 在内部注入 |
| 10 | 文档更新 | 同步 `loop/README.md`, `loop/WORKFLOW.md`, `rp5-serial/WORKFLOW.md` | 均反映 transcript 和 serial_context 实际用法 |

---

## 5. 架构设计

### 5.1 三层职责

```
Host (Windows)
  ├── serial_runtime: 环形 + transcript + ISO 时间戳
  ├── handler: 暴露 status.transcript_path、read_recent.entries
  │
Transport / Client (WSL2)
  ├── AutomationClient: 读取 structured recent entries + status
  ├── Rp5SerialTransport: 真实时间戳 + runtime_context (snippet/reboot_cycles/transcript)
  │
Loop Core
  ├── Collector: mode=serial_context → 拿 transport.describe_runtime_context()
  ├── Executor: 失败后聚合 serial context 到 bundle
  ├── Runner: _enrich_bundle 注入 serial_context
  ├── EvidenceBundle: serial_context 字段 → JSON + summary.txt
```

### 5.2 数据结构

#### Transcript 行模型
```python
# serial_runtime.py 内部条目
{
    "text": "init: starting service 'zygote'",
    "ts": "2026-06-20T12:00:01+0800",    # ISO 时间戳，host 侧赋予
    "pending": False                       # True 表示 RX buffer 中换行未完成的部分
}
```

#### EvidenceBundle 扩展
```python
# models.py
@dataclass
class EvidenceBundle:
    ...
    serial_context: dict = field(default_factory=dict)
    # {
    #     "transcript_path": str,
    #     "serial_snippet": [str, ...],     # 最近 N 行（不多于 40）
    #     "reboot_cycles": int,
    #     "recent_line_count": int,
    #     "recent_buffer_limit": int,
    # }
```

#### Transport 上下文接口
```python
# transport.py — Rp5SerialTransport
def describe_runtime_context(self) -> dict:
    return {
        "transcript_path": str,
        "serial_snippet": [str, ...],
        "reboot_cycles": int,
        "recent_line_count": int,
        "recent_buffer_limit": int,
    }
```

### 5.3 证据流

```
1. host 持续写 transcript（_append_entry → 文件）
2. host 将 transcript_path 写入 session.status
3. le run 启动时 runner 初始化 transport
4. 用例执行，失败时触发 collector
5. collector(serial_context) 调用 transport.describe_runtime_context()
6. 拿回 transcript_path + snippet + reboot_cycles
7. 处理器/executor 合并到 EvidenceBundle.serial_context
8. evidence.py 写入 JSON + summary.txt，含 full serial context
```

## 6. 文件变更清单

### 必须改
| 文件 | 改动要点 |
|------|---------|
| `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py` | 加入 transcript 落盘、结构化 entry、`recent_entries()`、扩大 buffer |
| `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/handler.py` | `session.status`/`stream.read_recent` 返回 entries + transcript 元数据 |
| `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py` | 接收 `--transcript-dir` CLI 参数 |
| `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py` | `capture_recent_entries()`、`fetch_status()` |
| `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py` | 用 host 时间戳构造 `ObservedLine`、`describe_runtime_context()`、`set_cycle_markers()` |
| `engineering/loop/core/python/loop_core/models.py` | `EvidenceBundle` 加 `serial_context` 字段 |
| `engineering/loop/core/python/loop_core/collector.py` | 支持 `mode: serial_context` |
| `engineering/loop/core/python/loop_core/runner.py` | `_enrich_bundle` 把 transport context 注入 bundle |
| `engineering/loop/core/python/loop_core/evidence.py` | 渲染 `serial_context` 到 JSON 和 summary |
| `engineering/loop/core/python/loop_core/config.py` | `DeviceProfile` 加 `serial_snippet_limit` |
| `engineering/loop/cases/common/shell.yaml` | 加 `serial_recent` collector |
| `engineering/loop/cases/system/boot-success.yaml` | `zygote_running` fail 时增加串口 collector |
| `engineering/loop/README.md` | 刷新字段和用法 |
| `engineering/loop/WORKFLOW.md` | 说明串口第一现场证据链 |
| `engineering/loop/connection/providers/rp5-serial/WORKFLOW.md` | 与实现对齐 |

### 对应测试
| 测试文件 | 覆盖 |
|---------|------|
| `test_monitor_flow.py` | recent entries + transcript 状态 |
| `test_session.py` | status 含 transcript 元数据 |
| `test_automation_client.py` | capture_recent_entries / fetch_status |
| `test_transport.py` | 真实时间戳、`describe_runtime_context`、reboot cycle |
| `test_collector.py` | serial_context mode、artifact paths |
| `test_runner.py` | `serial_context` 注入 bundle |
| `test_evidence.py` | JSON / summary 渲染 transcript/snippet/cycle |
| `test_executor.py` | shell fail 时保底 collector |

---

## 7. 验证策略

| 层级 | 命令 | 目标 |
|------|------|------|
| Provider 单测 | `pytest provider/python/tests/ -v` | transcript、时间戳、transport context |
| Core 单测 | `pytest core/python/tests/ -v` | bundle、collector、evidence |
| 联合回归 | `pytest core/ provider/ -v` | 全绿无回归破坏 |
| 手工冒烟（可选） | `le.sh run --suite boot-success ...` | 在真机上验证 EvidenceBundle 是否输出 `serial_context` |

---

## 8. 与前序 spec 的关系

本 spec 在以下前序 spec 基础之上扩展：

| 前序 spec | 关系 |
|-----------|------|
| `2026-06-19-loop-engineering-v2-design.md` | 当前基线架构；本次不推翻，只添加字段和能力 |
| `2026-06-20-loop-core-reliability-and-reuse-design.md` | 同一迭代周期；本次设计可安全叠加 |
| `2026-06-19-loop-engineering-design.md` | v1 架构参考；串口 transcript 已在此提出但未落地 |

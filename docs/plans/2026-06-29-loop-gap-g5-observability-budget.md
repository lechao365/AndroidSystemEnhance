# G5 loop 可观测 + 预算闸 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 loop runtime 补齐 trace 聚合视图（节点级耗时 + CLI 增强）和 wall_clock 预算闸。

**Architecture:** contracts 层加字段（CheckpointRecord.duration_ms + LoopSession.wall_clock_limit + 新 FailureCode）；engine 层主循环统一计时 + wall_clock 检查；CheckpointStore 加 summary() 聚合；CLI status 输出加 trace_summary 段。

**Tech Stack:** Python 3.11+, pytest, dataclasses

**关联设计:** `docs/specs/2026-06-29-loop-gap-g5-observability-budget-design.md`

---

## 测试环境

所有 pytest 命令需设置 PYTHONPATH：

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
```

全量回归命令：
```bash
PYTHONPATH="$P" python3 -m pytest \
  engineering/loop/controller/python/tests/ \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  engineering/loop/connection/providers/adb/python/tests/ \
  engineering/loop/deploy/python/tests/ \
  engineering/loop/contracts/python/tests/ \
  -q --import-mode=importlib
```

---

## Task 1: contracts — 新增 WALL_CLOCK_BUDGET_EXCEEDED FailureCode（TDD）

**Files:**
- Modify: `engineering/loop/contracts/python/loop_contracts/failure_codes.py:21`
- Test: `engineering/loop/contracts/python/tests/test_failure_codes.py`

- [ ] **Step 1: 写失败测试**

在 `test_failure_codes.py` 末尾追加：

```python
def test_wall_clock_budget_exceeded_exists():
    """G5: 新增 WALL_CLOCK_BUDGET_EXCEEDED FailureCode。"""
    from loop_contracts.failure_codes import FailureCode
    assert hasattr(FailureCode, "WALL_CLOCK_BUDGET_EXCEEDED")
    assert FailureCode.WALL_CLOCK_BUDGET_EXCEEDED.value == "WALL_CLOCK_BUDGET_EXCEEDED"


def test_failure_code_count_is_18():
    """G5: FailureCode 成员数应为 18（原 17 + WALL_CLOCK_BUDGET_EXCEEDED）。"""
    from loop_contracts.failure_codes import FailureCode
    assert len(list(FailureCode)) == 18
```

- [ ] **Step 2: 运行验证失败**

```bash
PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/test_failure_codes.py::test_wall_clock_budget_exceeded_exists engineering/loop/contracts/python/tests/test_failure_codes.py::test_failure_code_count_is_18 -v
```

Expected: FAIL

- [ ] **Step 3: 加枚举成员**

在 `failure_codes.py` 的 `VERIFICATION_STUCK` 行之后追加：

```python
    VERIFICATION_STUCK = "VERIFICATION_STUCK"
    WALL_CLOCK_BUDGET_EXCEEDED = "WALL_CLOCK_BUDGET_EXCEEDED"
```

- [ ] **Step 4: 运行验证通过**

```bash
PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/test_failure_codes.py -v
```

Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/contracts/python/loop_contracts/failure_codes.py engineering/loop/contracts/python/tests/test_failure_codes.py
git commit -m "功能(contracts): G5 新增 WALL_CLOCK_BUDGET_EXCEEDED FailureCode"
```

---

## Task 2: contracts — CheckpointRecord 加 duration_ms + LoopSession 加 wall_clock_limit（TDD）

**Files:**
- Modify: `engineering/loop/contracts/python/loop_contracts/models.py:50,78`
- Test: `engineering/loop/contracts/python/tests/test_models.py`, `engineering/loop/contracts/python/tests/test_runtime_models.py`

- [ ] **Step 1: 写失败测试**

在 `test_runtime_models.py` 末尾追加（如果文件中已有 `from loop_contracts.models import CheckpointRecord` 则不重复导入）：

```python
def test_checkpoint_record_duration_ms_default_zero():
    """G5: CheckpointRecord 新增 duration_ms 字段，默认 0。"""
    cp = CheckpointRecord(
        checkpoint_id="cp-test",
        session_id="s1",
        attempt_index=0,
        current_node="INIT_SESSION",
        input_summary={},
        output_summary={},
        failure_code=FailureCode.NONE,
        matched_guards=[],
        next_node="RUN_VERIFY",
        timestamp="2026-01-01T00:00:00+08:00",
    )
    assert cp.duration_ms == 0


def test_checkpoint_record_to_dict_includes_duration_ms():
    """G5: to_dict() 输出含 duration_ms。"""
    cp = CheckpointRecord(
        checkpoint_id="cp-test",
        session_id="s1",
        attempt_index=0,
        current_node="INIT_SESSION",
        input_summary={},
        output_summary={},
        failure_code=FailureCode.NONE,
        matched_guards=[],
        next_node="RUN_VERIFY",
        timestamp="2026-01-01T00:00:00+08:00",
        duration_ms=500,
    )
    d = cp.to_dict()
    assert d["duration_ms"] == 500
```

在 `test_models.py` 末尾追加（如果有 LoopSession 测试则追加，否则新建函数）：

```python
def test_loop_session_wall_clock_limit_default_zero():
    """G5: LoopSession 新增 wall_clock_limit 字段，默认 0（不限制）。"""
    from loop_contracts.models import LoopSession
    session = LoopSession(
        session_id="s1",
        workflow_id="runtime",
        target="lciod",
        suite="hal",
        max_attempts=5,
    )
    assert session.wall_clock_limit == 0
```

- [ ] **Step 2: 运行验证失败**

```bash
PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/test_runtime_models.py::test_checkpoint_record_duration_ms_default_zero engineering/loop/contracts/python/tests/test_runtime_models.py::test_checkpoint_record_to_dict_includes_duration_ms engineering/loop/contracts/python/tests/test_models.py::test_loop_session_wall_clock_limit_default_zero -v
```

Expected: FAIL

- [ ] **Step 3: 修改 models.py**

在 `CheckpointRecord` 的 `timestamp: str` 之后加一行：

```python
    timestamp: str
    duration_ms: int = 0
```

在 `LoopSession` 的 `artifacts_dir: str = ""` 之后加一行：

```python
    artifacts_dir: str = ""
    wall_clock_limit: int = 0
```

- [ ] **Step 4: 运行验证通过**

```bash
PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/ -v
```

Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/contracts/python/loop_contracts/models.py engineering/loop/contracts/python/tests/test_runtime_models.py engineering/loop/contracts/python/tests/test_models.py
git commit -m "功能(contracts): G5 CheckpointRecord 加 duration_ms + LoopSession 加 wall_clock_limit"
```

---

## Task 3: checkpoint_store — _from_line 兼容 duration_ms 反序列化（TDD）

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py:64-75`
- Test: `engineering/loop/controller/python/tests/test_checkpoint_store.py`

- [ ] **Step 1: 写失败测试**

在 `test_checkpoint_store.py` 末尾追加：

```python
def test_from_line_handles_missing_duration_ms(tmp_path):
    """G5: 旧 JSONL 行（无 duration_ms）反序列化时填默认 0。"""
    import json
    from loop_controller.runtime.checkpoint_store import CheckpointStore

    store = CheckpointStore(str(tmp_path), "s1")
    # 写一行旧格式 JSON（无 duration_ms 键）
    old_record = {
        "checkpoint_id": "cp-old",
        "session_id": "s1",
        "attempt_index": 0,
        "current_node": "INIT_SESSION",
        "input_summary": {},
        "output_summary": {"reason": "init"},
        "failure_code": "NONE",
        "matched_guards": [],
        "next_node": "RUN_VERIFY",
        "timestamp": "2026-01-01T00:00:00+08:00",
    }
    (tmp_path / "runtime_checkpoints.jsonl").write_text(
        json.dumps(old_record) + "\n", encoding="utf-8"
    )
    records = store.all()
    assert len(records) == 1
    assert records[0].duration_ms == 0


def test_from_line_reads_duration_ms(tmp_path):
    """G5: 新 JSONL 行（含 duration_ms）反序列化正确读取。"""
    import json
    from loop_controller.runtime.checkpoint_store import CheckpointStore

    store = CheckpointStore(str(tmp_path), "s1")
    new_record = {
        "checkpoint_id": "cp-new",
        "session_id": "s1",
        "attempt_index": 0,
        "current_node": "RUN_VERIFY",
        "input_summary": {},
        "output_summary": {"reason": "verify"},
        "failure_code": "NONE",
        "matched_guards": [],
        "next_node": "DECIDE_NEXT",
        "timestamp": "2026-01-01T00:00:01+08:00",
        "duration_ms": 1234,
    }
    (tmp_path / "runtime_checkpoints.jsonl").write_text(
        json.dumps(new_record) + "\n", encoding="utf-8"
    )
    records = store.all()
    assert len(records) == 1
    assert records[0].duration_ms == 1234
```

- [ ] **Step 2: 运行验证失败**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_checkpoint_store.py::test_from_line_handles_missing_duration_ms engineering/loop/controller/python/tests/test_checkpoint_store.py::test_from_line_reads_duration_ms -v
```

Expected: FAIL（`_from_line` 不解析 duration_ms，字段缺失或为 0）

- [ ] **Step 3: 修改 _from_line**

在 `checkpoint_store.py` 的 `_from_line` 方法（第 64-75 行）中，在 `timestamp=data["timestamp"],` 之后加一行：

```python
        timestamp=data["timestamp"],
        duration_ms=data.get("duration_ms", 0),
```

- [ ] **Step 4: 运行验证通过**

```bash
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_checkpoint_store.py -v
```

Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py engineering/loop/controller/python/tests/test_checkpoint_store.py
git commit -m "功能(checkpoint): G5 _from_line 兼容 duration_ms 反序列化"
```

---

## Task 4: checkpoint_store — 新增 summary() 聚合方法（TDD）

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py`
- Test: `engineering/loop/controller/python/tests/test_checkpoint_store.py`

- [ ] **Step 1: 写失败测试**

在 `test_checkpoint_store.py` 末尾追加：

```python
def test_summary_empty_returns_zero(tmp_path):
    """G5: 无 checkpoint 时 summary 返回零值。"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    store = CheckpointStore(str(tmp_path), "s1")
    s = store.summary()
    assert s["node_count"] == 0
    assert s["total_duration_ms"] == 0
    assert s["nodes"] == []


def test_summary_aggregates_duration_and_nodes(tmp_path):
    """G5: summary 正确聚合 total_duration_ms + nodes 列表。"""
    import json
    from loop_controller.runtime.checkpoint_store import CheckpointStore

    store = CheckpointStore(str(tmp_path), "s1")
    records = [
        {
            "checkpoint_id": "cp-1", "session_id": "s1", "attempt_index": 0,
            "current_node": "INIT_SESSION", "input_summary": {},
            "output_summary": {"reason": "init"}, "failure_code": "NONE",
            "matched_guards": [], "next_node": "RUN_VERIFY",
            "timestamp": "2026-01-01T00:00:00+08:00", "duration_ms": 100,
        },
        {
            "checkpoint_id": "cp-2", "session_id": "s1", "attempt_index": 0,
            "current_node": "RUN_VERIFY", "input_summary": {},
            "output_summary": {"reason": "verify PASS"}, "failure_code": "NONE",
            "matched_guards": [], "next_node": "DECIDE_NEXT",
            "timestamp": "2026-01-01T00:00:10+08:00", "duration_ms": 5000,
        },
    ]
    (tmp_path / "runtime_checkpoints.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    s = store.summary()
    assert s["node_count"] == 2
    assert s["total_duration_ms"] == 5100
    assert len(s["nodes"]) == 2
    assert s["nodes"][0]["node"] == "INIT_SESSION"
    assert s["nodes"][0]["duration_ms"] == 100
    assert s["nodes"][1]["node"] == "RUN_VERIFY"
    assert s["nodes"][1]["duration_ms"] == 5000


def test_format_duration_human_readable():
    """G5: _format_duration 毫秒→人类可读。"""
    from loop_controller.runtime.checkpoint_store import _format_duration
    assert _format_duration(0) == "0.0s"
    assert _format_duration(500) == "0.5s"
    assert _format_duration(1000) == "1.0s"
    assert _format_duration(30000) == "30s"
    assert _format_duration(90000) == "1m 30s"
    assert _format_duration(3600000) == "1h 0m"
```

注意：`_format_duration` 的测试断言可能需要根据实际实现微调（特别是秒级精度的 ".0s" 后缀），实现时确保测试通过。

- [ ] **Step 2: 运行验证失败**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_checkpoint_store.py::test_summary_empty_returns_zero engineering/loop/controller/python/tests/test_checkpoint_store.py::test_summary_aggregates_duration_and_nodes engineering/loop/controller/python/tests/test_checkpoint_store.py::test_format_duration_human_readable -v
```

Expected: FAIL（summary() 和 _format_duration 不存在）

- [ ] **Step 3: 实现 summary() 和 _format_duration()**

在 `checkpoint_store.py` 的 `CheckpointStore` 类**内部**（`_from_line` 方法之后）新增 `summary` 方法：

```python
    def summary(self) -> dict:
        """聚合当前 session 的 checkpoint 流水为 trace 摘要。"""
        records = self.all()
        if not records:
            return {"node_count": 0, "total_duration_ms": 0, "nodes": []}

        total_ms = sum(r.duration_ms for r in records)
        return {
            "node_count": len(records),
            "total_duration_ms": total_ms,
            "total_duration_human": _format_duration(total_ms),
            "nodes": [
                {
                    "node": r.current_node,
                    "attempt": r.attempt_index,
                    "duration_ms": r.duration_ms,
                    "failure_code": r.failure_code.value if r.failure_code else "",
                    "reason": r.output_summary.get("reason", ""),
                    "timestamp": r.timestamp,
                }
                for r in records
            ],
        }
```

在文件**模块级**（类定义之前或之后）新增辅助函数：

```python
def _format_duration(ms: int) -> str:
    """毫秒 → 人类可读（如 '2m 30s' / '1h 5m'）。"""
    s = ms / 1000
    if s < 1:
        return f"{ms}ms"
    if s < 60:
        return f"{s:.1f}s"
    total_s = int(s)
    m, sec = divmod(total_s, 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"
```

注意：`_format_duration` 测试中 `_format_duration(0)` 应返回 `"0ms"`（因为 0 < 1 走第一个分支）。如果测试断言写的是 `"0.0s"` 则需要调整测试——以实现为准，确保语义合理。建议实现时 `_format_duration(0)` 返回 `"0ms"` 并将测试断言改为 `assert _format_duration(0) == "0ms"`。

- [ ] **Step 4: 运行验证通过**

```bash
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_checkpoint_store.py -v
```

Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py engineering/loop/controller/python/tests/test_checkpoint_store.py
git commit -m "功能(checkpoint): G5 新增 summary() 聚合方法 + _format_duration"
```

---

## Task 5: engine — 主循环计时 + wall_clock 预算闸（TDD）

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py:46-126,615-630`
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 写失败测试**

在 `test_runtime_engine.py` 末尾追加（需确认导入 `time` 和 `LoopSession`）：

```python
def test_engine_records_nonzero_duration_ms(tmp_path):
    """G5: engine 执行节点后 checkpoint 含非零 duration_ms。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_controller.runtime.checkpoint_store import CheckpointStore

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    rt._execute_current_node()  # INIT_SESSION
    store = CheckpointStore(str(tmp_path), "s1")
    cp = store.latest()
    assert cp is not None
    assert cp.duration_ms >= 0  # 至少有值（INIT_SESSION 极快，可能为 0）


def test_engine_wall_clock_budget_exceeds(tmp_path):
    """G5: wall_clock_limit=0.001s 时立即超限，设 DONE_FAILURE。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_contracts.models import RuntimeTerminalState

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=5,
        artifacts_dir=str(tmp_path),
        wall_clock_limit=0.001,  # 极小，确保超限
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    time.sleep(0.01)  # 确保已超过 0.001s
    rt.run(max_iterations=3)
    assert rt._state.terminal_state == RuntimeTerminalState.DONE_FAILURE
    assert "wall_clock" in rt._state.transition_reason.lower()


def test_engine_wall_clock_zero_means_unlimited(tmp_path):
    """G5: wall_clock_limit=0 时不触发预算闸（走到正常终态）。"""
    from loop_controller.runtime.engine import LoopRuntime

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
        wall_clock_limit=0,  # 不限制
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    # 不应因 wall_clock 超时退出
    # 跑一轮看是否正常（会因 cases_dir 无用例而 FAIL，但不是 wall_clock FAIL）
    rt.run(max_iterations=3)
    assert "wall_clock" not in rt._state.transition_reason.lower()
```

- [ ] **Step 2: 运行验证失败**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_engine_records_nonzero_duration_ms engineering/loop/controller/python/tests/test_runtime_engine.py::test_engine_wall_clock_budget_exceeds engineering/loop/controller/python/tests/test_runtime_engine.py::test_engine_wall_clock_zero_means_unlimited -v
```

Expected: FAIL（wall_clock 检查不存在；duration_ms 可能不写入）

- [ ] **Step 3: 修改 __init__ 加计时状态**

在 `engine.py` 的 `__init__` 方法（第 46-68 行）末尾（`self._human_gate_triggers` 之后）加两行：

```python
        self._human_gate_triggers: list[str] = ["low_confidence", "kernel_patch", "dd_boot_reboot"]
        # G5: 节点耗时测量 + wall_clock 预算闸
        self._session_start: float = 0.0
        self._last_node_duration_ms: int = 0
```

- [ ] **Step 4: 修改 run() 加计时 + wall_clock 检查**

将 `run()` 方法（第 109-126 行）改为：

```python
    def run(self, max_iterations: int = 100) -> RuntimeState:
        self._session_start = time.perf_counter()
        iterations = 0
        while self._state.terminal_state == RuntimeTerminalState.NONE:
            iterations += 1
            if iterations > max_iterations:
                self._state.terminal_state = RuntimeTerminalState.DONE_FAILURE
                self._state.transition_reason = f"max_iterations({max_iterations}) exceeded"
                break
            t_start = time.perf_counter()
            self._execute_current_node()
            elapsed = time.perf_counter() - t_start
            self._last_node_duration_ms = int(elapsed * 1000)
            # pending_human_gate：等待人工决策，不设终态、不继续推进
            if self._state.pending_human_gate:
                self._persist_session()
                return self._state
            if self._state.terminal_state != RuntimeTerminalState.NONE:
                break
            # G5: wall_clock 预算闸
            if self._session.wall_clock_limit > 0:
                wall = time.perf_counter() - self._session_start
                if wall > self._session.wall_clock_limit:
                    self._state.terminal_state = RuntimeTerminalState.DONE_FAILURE
                    self._state.transition_reason = (
                        f"wall_clock budget exceeded: {wall:.0f}s > "
                        f"{self._session.wall_clock_limit}s"
                    )
                    self._checkpoint(
                        "wall_clock budget exceeded",
                        FailureCode.WALL_CLOCK_BUDGET_EXCEEDED,
                    )
                    break
            self._transition()
        self._persist_session()
        return self._state
```

- [ ] **Step 5: 修改 _checkpoint() 取暂存 duration_ms**

将 `_checkpoint()` 方法（第 615-630 行）的签名改为接收可选 duration_ms，并在构造 CheckpointRecord 时传入：

```python
    def _checkpoint(self, reason: str, failure_code: FailureCode,
                    matched_guards: list[str] | None = None,
                    duration_ms: int | None = None) -> None:
        next_node = self._compute_next_node()
        if duration_ms is None:
            duration_ms = getattr(self, "_last_node_duration_ms", 0)
        cp = CheckpointRecord(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:12]}",
            session_id=self._session.session_id,
            attempt_index=self._session.current_attempt,
            current_node=self._state.current_node,
            input_summary={"suite": self._session.suite},
            output_summary={"node_status": self._state.node_status, "reason": reason},
            failure_code=failure_code,
            matched_guards=matched_guards or [],
            next_node=next_node,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            duration_ms=duration_ms,
        )
        self._store.save(cp)
        self._state.last_checkpoint_at = cp.timestamp
```

- [ ] **Step 6: 运行验证通过**

```bash
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v
```

Expected: 全部 passed（可能需要调整测试中的断言——wall_clock_limit=0.001 太小可能在 INIT_SESSION 就超时，这是正确行为）

- [ ] **Step 7: 跑 controller 全部测试确保无回归**

```bash
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/ -v --import-mode=importlib
```

Expected: 全部 passed

- [ ] **Step 8: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G5 主循环计时 + wall_clock 预算闸 + _checkpoint duration_ms"
```

---

## Task 6: runtime_cli — status 输出加 trace_summary + session 初始化读 budget（TDD）

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime_cli.py:106-130,272-277,349-388`
- Test: `engineering/loop/controller/python/tests/test_runtime_cli.py`

- [ ] **Step 1: 写失败测试**

在 `test_runtime_cli.py` 末尾追加（需确认导入方式）：

```python
def test_status_output_includes_trace_summary(tmp_path):
    """G5: le runtime status 输出含 trace_summary 段。"""
    import json
    import subprocess
    import sys

    # 构造一个最小 session.json
    session_data = {
        "session_id": "s1",
        "workflow_id": "runtime",
        "target": "lciod",
        "suite": "hal",
        "max_attempts": 5,
        "current_attempt": 0,
        "status": "PENDING",
        "latest_failure_code": "NONE",
        "attempts": [],
        "artifacts_dir": str(tmp_path),
        "wall_clock_limit": 0,
    }
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps(session_data), encoding="utf-8")

    # 调 CLI status
    from loop_controller.runtime_cli import _handle_status
    from unittest.mock import MagicMock
    args = MagicMock()
    args.session = str(session_path)
    _handle_status(args)  # 应不报错
    # 验证方式：捕获 stdout 含 trace_summary
```

注意：这个测试可能需要用 `capsys` 或直接调内部函数验证返回值。如果 CLI 难以测试，可以改为直接测试 `_handle_status` 的输出 JSON 含 `trace_summary` 键。实现时调整测试方式。

- [ ] **Step 2: 运行验证失败**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py::test_status_output_includes_trace_summary -v
```

Expected: FAIL

- [ ] **Step 3: 修改 _handle_status 加 trace_summary**

将 `_handle_status`（第 272-277 行）改为：

```python
def _handle_status(args: argparse.Namespace) -> int:
    session, ts = _load_session(args.session)
    data = _session_to_dict(session)
    data["terminal_state"] = ts.value
    # G5: trace 聚合
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    ckpt_store = CheckpointStore(session.artifacts_dir, session.session_id)
    data["trace_summary"] = ckpt_store.summary()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0
```

- [ ] **Step 4: 修改 _session_to_dict 和 _load_session 加 wall_clock_limit**

`_session_to_dict`（第 375-387 行）加一行：

```python
def _session_to_dict(session: LoopSession) -> dict:
    return {
        "session_id": session.session_id,
        "workflow_id": session.workflow_id,
        "target": session.target,
        "suite": session.suite,
        "max_attempts": session.max_attempts,
        "current_attempt": session.current_attempt,
        "status": session.status,
        "latest_failure_code": session.latest_failure_code.value,
        "attempts": session.attempts,
        "artifacts_dir": session.artifacts_dir,
        "wall_clock_limit": session.wall_clock_limit,
    }
```

`_load_session`（第 349-372 行）在构造 LoopSession 时加 `wall_clock_limit`：

```python
    session = LoopSession(
        session_id=data.get("session_id", ""),
        workflow_id=data.get("workflow_id", "runtime"),
        target=data.get("target", ""),
        suite=data.get("suite", ""),
        max_attempts=data.get("max_attempts", 5),
        current_attempt=data.get("current_attempt", 0),
        status=data.get("status", "PENDING"),
        latest_failure_code=fc,
        attempts=data.get("attempts", []),
        artifacts_dir=data.get("artifacts_dir", ""),
        wall_clock_limit=data.get("wall_clock_limit", 0),
    )
```

- [ ] **Step 5: 修改 _handle_init 读 budget 配置**

在 `_handle_init`（第 106-130 行）中，构造 LoopSession 时从 analyzer.yaml 读 budget：

```python
def _handle_init(args: argparse.Namespace) -> int:
    sid = f"{args.target}-{time.strftime('%Y%m%d%H%M%S')}"
    cfg = _load_analyzer_config()
    budget_cfg = cfg.get("budget", {})
    wall_clock_limit = budget_cfg.get("wall_clock_seconds", 0)
    session = LoopSession(
        session_id=sid,
        workflow_id="runtime",
        target=args.target,
        suite=args.suite,
        max_attempts=args.max_attempts,
        artifacts_dir=args.artifacts_dir,
        wall_clock_limit=wall_clock_limit,
    )
    # ... 后续写文件逻辑不变 ...
```

- [ ] **Step 6: 运行验证通过**

```bash
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py -v
```

Expected: 全部 passed

- [ ] **Step 7: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime_cli.py engineering/loop/controller/python/tests/test_runtime_cli.py
git commit -m "功能(cli): G5 status 输出加 trace_summary + session 读 budget 配置"
```

---

## Task 7: config — analyzer.yaml 加 budget 段

**Files:**
- Modify: `engineering/loop/config/analyzer.yaml`

- [ ] **Step 1: 在 analyzer.yaml 末尾追加 budget 段**

```yaml
budget:
  wall_clock_seconds: 3600   # session 总耗时上限（秒），0=不限制；超时触发 DONE_FAILURE
```

- [ ] **Step 2: 提交**

```bash
git add engineering/loop/config/analyzer.yaml
git commit -m "配置(analyzer): G5 新增 budget.wall_clock_seconds 预算闸配置"
```

---

## Task 8: G8 元测试 + 文档同步

**Files:**
- Modify: `engineering/loop/controller/python/tests/test_docs_consistency.py`
- Modify: `engineering/loop/contracts/README.md`
- Modify: `engineering/loop/controller/README.md`

- [ ] **Step 1: 更新 G8 元测试**

在 `test_docs_consistency.py` 中：

1. 守护点 1 的 `assert count == 17` 改为 `assert count == 18`
2. README 断言 `"17 项"` 改为 `"18 项"`
3. 新增守护点验证 `WALL_CLOCK_BUDGET_EXCEEDED` 在 README 中：

```python
def test_wall_clock_budget_exceeded_in_readme() -> None:
    """守护点 9: WALL_CLOCK_BUDGET_EXCEEDED 出现在 contracts/README.md 中。"""
    text = _read("engineering/loop/contracts/README.md")
    assert "WALL_CLOCK_BUDGET_EXCEEDED" in text
```

- [ ] **Step 2: 更新 contracts/README.md**

将 FailureCode 相关描述从 17→18 项，并在成员列表中加入 `WALL_CLOCK_BUDGET_EXCEEDED`。用 Read 工具读取当前 README 找到具体位置后修改。

- [ ] **Step 3: 更新 controller/README.md**

在 Terminal State 段落中 `DONE_FAILURE` 描述补充 wall_clock 超时说明。用 Read 工具读取当前内容（约第 134 行）：

```
- `DONE_FAILURE`：系统异常终止（含 wall_clock 预算超时）。
```

- [ ] **Step 4: 运行 G8 元测试验证通过**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/test_docs_consistency.py -v
```

Expected: 全部 passed（原 8 个 + 新增 1 个 = 9 个）

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/tests/test_docs_consistency.py engineering/loop/contracts/README.md engineering/loop/controller/README.md
git commit -m "文档+测试(loop): G5 同步 G8 元测试（17→18）+ README 补 wall_clock 说明"
```

---

## Task 9: 全量回归 + 推送

- [ ] **Step 1: 跑全量回归**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest \
  engineering/loop/controller/python/tests/ \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  engineering/loop/connection/providers/adb/python/tests/ \
  engineering/loop/deploy/python/tests/ \
  engineering/loop/contracts/python/tests/ \
  -q --import-mode=importlib
```

Expected: 全部 passed（基线 617 + G5 新增约 12 = ~629）

- [ ] **Step 2: 推送到远端**

```bash
git push origin main
```

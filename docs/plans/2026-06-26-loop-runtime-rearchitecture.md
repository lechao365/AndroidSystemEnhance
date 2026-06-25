# Loop Runtime 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用自研零依赖状态图 runtime 替换当前 loop 的旧编排架构，同时保留并增强 connection/cases/loop_core/deploy/contracts 作为长期能力层。

**Architecture:** 先提纯可复用能力层与契约层，再落地 runtime 核心（state/guard/transition/checkpoint/runtime CLI），之后以新 runtime 接管 verify→decide→analyze→patch→compile→deploy→rerun 全链路，最后在完成金丝雀验证后删除旧 control/workflow 编排层。迁移期允许旧架构短暂存在，但仅用于对照验证与风险兜底；最终态只保留新 runtime。

**Tech Stack:** Python 3.11+、argparse、dataclasses、StrEnum、pytest、git、adb、rp5-serial provider、AOSP build tools、现有 harness path/observability 能力。

**设计文档:** `docs/specs/2026-06-26-loop-runtime-rearchitecture-design.md`

---

## File Structure

### 长期保留并增强的能力层文件
- Modify: `engineering/loop/connection/README.md`
- Modify: `engineering/loop/core/python/loop_core/cli.py`
- Modify: `engineering/loop/core/python/loop_core/runner.py`
- Modify: `engineering/loop/core/python/loop_core/evidence.py`
- Modify: `engineering/loop/deploy/python/loop_deploy/cli.py`
- Modify: `engineering/loop/deploy/python/loop_deploy/deployer.py`
- Modify: `engineering/loop/deploy/python/loop_deploy/compiler.py`
- Modify: `engineering/loop/deploy/python/loop_deploy/rollback.py`
- Modify: `engineering/loop/contracts/python/loop_contracts/models.py`
- Modify: `engineering/loop/contracts/python/loop_contracts/failure_codes.py`
- Modify: `engineering/loop/contracts/python/loop_contracts/__init__.py`

### 新增 runtime 与阶段能力文件
- Create: `engineering/loop/controller/python/loop_controller/stages.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/__init__.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/types.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/guards.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/engine.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/nodes.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime_cli.py`

### 新增测试文件
- Create: `engineering/loop/contracts/python/tests/test_runtime_models.py`
- Create: `engineering/loop/controller/python/tests/test_stages.py`
- Create: `engineering/loop/controller/python/tests/test_runtime_guards.py`
- Create: `engineering/loop/controller/python/tests/test_checkpoint_store.py`
- Create: `engineering/loop/controller/python/tests/test_runtime_engine.py`
- Create: `engineering/loop/controller/python/tests/test_runtime_cli.py`

### 迁移后删除的旧编排文件（执行前必须按项目规则列出清单并再次向用户确认）
- Delete later: `engineering/loop/controller/python/loop_controller/control_cli.py`
- Delete later: `engineering/loop/controller/python/loop_controller/engine.py`
- Delete later: `engineering/loop/controller/python/loop_controller/policy.py`
- Delete later: `engineering/loop/controller/python/loop_controller/state.py`
- Delete later: `engineering/loop/controller/python/tests/test_control_cli.py`
- Delete later: `engineering/loop/controller/python/tests/test_engine.py`
- Delete later: `engineering/loop/controller/python/tests/test_policy.py`
- Delete later: `engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh`
- Delete later: `engineering/loop/workflows/README.md`（重写前先确认是否整目录删除或仅清理旧内容）

### 文档与入口重写
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/loop/controller/README.md`
- Modify: `engineering/loop/scripts/README.md`
- Modify: `engineering/loop/scripts/le.sh`
- Create: `docs/plans/2026-06-26-loop-runtime-rearchitecture.md`

---

## 统一约定（所有任务适用）

**测试环境命令（仓库根执行）：**
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest <test_file>::<test_name> -v
```

**回归命令：**
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/contracts/python/tests/ engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/deploy/python/tests/ -v
```

**提交风格：** `feat(loop-runtime): ...` / `refactor(loop-runtime): ...` / `docs(loop-runtime): ...`

**删除规则提醒：** 任何文件删除都必须在实际执行前列出具体文件并等待用户再次确认；本计划只设计删除阶段，不授权实现时跳过确认。

---

### Task 1: 扩展 contracts 为 runtime 双层状态模型

**Files:**
- Modify: `engineering/loop/contracts/python/loop_contracts/models.py`
- Modify: `engineering/loop/contracts/python/loop_contracts/failure_codes.py`
- Modify: `engineering/loop/contracts/python/loop_contracts/__init__.py`
- Modify: `engineering/loop/contracts/python/tests/test_models.py`
- Create: `engineering/loop/contracts/python/tests/test_runtime_models.py`

- [ ] **Step 1: 先写失败测试，锁定 runtime contracts 的目标形态**

```python
from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import (
    CheckpointRecord,
    LoopSession,
    RuntimeState,
    RuntimeTerminalState,
)


def test_runtime_state_defaults():
    state = RuntimeState(current_node="INIT_SESSION")
    assert state.previous_node == ""
    assert state.node_status == "PENDING"
    assert state.pending_human_gate is False
    assert state.terminal_state == RuntimeTerminalState.NONE


def test_loop_session_tracks_attempts_and_failure_code():
    session = LoopSession(
        session_id="sess-001",
        workflow_id="runtime",
        target="lciod",
        suite="engineering/loop/cases/features/lciod/hal.yaml",
        max_attempts=5,
    )
    assert session.current_attempt == 0
    assert session.latest_failure_code == FailureCode.NONE
    assert session.attempts == []


def test_checkpoint_record_serializable():
    cp = CheckpointRecord(
        checkpoint_id="cp-001",
        session_id="sess-001",
        attempt_index=1,
        current_node="RUN_VERIFY",
        input_summary={"suite": "hal.yaml"},
        output_summary={"verify_result": "FAIL"},
        failure_code=FailureCode.RUN_FAILED,
        matched_guards=["attempts_below_limit"],
        next_node="BUILD_ANALYSIS_REQUEST",
        timestamp="2026-06-26T12:00:00+08:00",
    )
    data = cp.to_dict()
    assert data["failure_code"] == "RUN_FAILED"
    assert data["next_node"] == "BUILD_ANALYSIS_REQUEST"
```

- [ ] **Step 2: 运行 contracts 测试，确认先失败**

Run:
```bash
export PYTHONPATH="engineering/loop/contracts/python"
python3 -m pytest engineering/loop/contracts/python/tests/test_runtime_models.py -v
```

Expected:
- FAIL with `ImportError` / `AttributeError` because `LoopSession`、`RuntimeState`、`CheckpointRecord`、`RuntimeTerminalState` 尚不存在。

- [ ] **Step 3: 在 `failure_codes.py` 增补 runtime 需要的失败码**

```python
from enum import StrEnum


class FailureCode(StrEnum):
    NONE = "NONE"
    RUN_FAILED = "RUN_FAILED"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    DEPLOY_FATAL = "DEPLOY_FATAL"
    SESSION_STATE_ERROR = "SESSION_STATE_ERROR"
    COMPILE_FAILED = "COMPILE_FAILED"
    PATCH_REJECTED = "PATCH_REJECTED"
    BOOT_TIMEOUT_ROLLBACK = "BOOT_TIMEOUT_ROLLBACK"
    DUPLICATE_PATCH = "DUPLICATE_PATCH"
    KERNEL_DEAD_NO_SHELL = "KERNEL_DEAD_NO_SHELL"
    TRANSPORT_UNRECOVERABLE = "TRANSPORT_UNRECOVERABLE"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
```

- [ ] **Step 4: 在 `models.py` 定义新旧并存期的双层状态模型**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from loop_contracts.failure_codes import FailureCode


class RuntimeTerminalState(StrEnum):
    NONE = "NONE"
    DONE_SUCCESS = "DONE_SUCCESS"
    DONE_FAILURE = "DONE_FAILURE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"


@dataclass
class StageResult:
    stage_name: str
    status: str
    failure_code: FailureCode = FailureCode.NONE
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    next_action_hint: str = ""


@dataclass
class AttemptState:
    attempt_index: int
    stage_results: list[StageResult] = field(default_factory=list)
    run_result_ref: str = ""
    diagnosis_result_ref: str = ""
    patch_result_ref: str = ""
    deploy_result_ref: str = ""
    verify_result_ref: str = ""
    attempt_decision: str = ""


@dataclass
class LoopSession:
    session_id: str
    workflow_id: str
    target: str
    suite: str
    max_attempts: int
    current_attempt: int = 0
    status: str = "PENDING"
    termination_reason: str = ""
    latest_failure_code: FailureCode = FailureCode.NONE
    attempts: list[AttemptState] = field(default_factory=list)
    artifacts_dir: str = ""


@dataclass
class RuntimeState:
    current_node: str
    previous_node: str = ""
    node_status: str = "PENDING"
    transition_reason: str = ""
    pending_human_gate: bool = False
    interrupted: bool = False
    resume_token: str = ""
    last_checkpoint_at: str = ""
    terminal_state: RuntimeTerminalState = RuntimeTerminalState.NONE


@dataclass
class CheckpointRecord:
    checkpoint_id: str
    session_id: str
    attempt_index: int
    current_node: str
    input_summary: dict[str, object]
    output_summary: dict[str, object]
    failure_code: FailureCode
    matched_guards: list[str]
    next_node: str
    timestamp: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["failure_code"] = self.failure_code.value
        return data
```

- [ ] **Step 5: 更新 `__init__.py` 与现有模型测试，保证导出一致**

```python
from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import (
    AttemptState,
    CheckpointRecord,
    LoopSession,
    RuntimeState,
    RuntimeTerminalState,
    StageResult,
    TerminationDecision,
)

__all__ = [
    "AttemptState",
    "CheckpointRecord",
    "FailureCode",
    "LoopSession",
    "RuntimeState",
    "RuntimeTerminalState",
    "StageResult",
    "TerminationDecision",
]
```

- [ ] **Step 6: 运行 contracts 全量测试，确认全部通过**

Run:
```bash
export PYTHONPATH="engineering/loop/contracts/python"
python3 -m pytest engineering/loop/contracts/python/tests/ -v
```

Expected:
- PASS for existing contract tests and new runtime model tests.

- [ ] **Step 7: 提交**

```bash
git add engineering/loop/contracts/python/loop_contracts/models.py engineering/loop/contracts/python/loop_contracts/failure_codes.py engineering/loop/contracts/python/loop_contracts/__init__.py engineering/loop/contracts/python/tests/test_models.py engineering/loop/contracts/python/tests/test_runtime_models.py
git commit -m "feat(loop-runtime): add runtime contracts and failure codes"
```

---

### Task 2: 从旧 control_cli 中提取可复用阶段 handlers

**Files:**
- Create: `engineering/loop/controller/python/loop_controller/stages.py`
- Modify: `engineering/loop/controller/python/loop_controller/control_cli.py`
- Create: `engineering/loop/controller/python/tests/test_stages.py`
- Modify: `engineering/loop/controller/python/tests/test_control_cli.py`

- [ ] **Step 1: 先写失败测试，锁定 `stages.py` 的函数边界**

```python
import json
from pathlib import Path

from loop_controller.stages import analyze_request_stage, decide_stage, run_verify_stage


def test_run_verify_stage_reads_evidence_bundle(tmp_path, monkeypatch):
    bundle = {
        "summary": {"overall": "FAIL", "total": 1, "passed": 0, "failed": 1, "skipped": 0},
        "cases": [{"id": "case.fail", "status": "fail", "failure_reason": "boom", "command": "echo boom"}],
    }
    session = {
        "session_id": "sess-001",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 0,
        "max_attempts": 5,
        "attempts": [],
        "status": "PENDING",
    }
    Path(tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")

    def fake_run(cmd, **kwargs):
        Path(tmp_path / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        class R:
            returncode = 1
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    updated, stage = run_verify_stage(str(tmp_path / "session.json"), "test.yaml", "")
    assert updated["current_attempt"] == 1
    assert updated["attempts"][-1]["failed_cases"][0]["id"] == "case.fail"
    assert stage.failure_code.value == "RUN_FAILED"


def test_decide_stage_detects_duplicate_patch():
    session = {
        "current_attempt": 2,
        "max_attempts": 5,
        "status": "FAIL",
        "attempts": [
            {"attempt_index": 1, "failure_code": "RUN_FAILED", "patch_applied": {"patch_hash": "aaa"}},
            {"attempt_index": 2, "failure_code": "COMPILE_FAILED", "patch_applied": {"patch_hash": "aaa"}},
        ],
    }
    decision = decide_stage(session)
    assert decision["decision"] == "STOP"
    assert decision["reason"] == "duplicate_patch_detected"


def test_analyze_request_stage_writes_json(tmp_path):
    session = {
        "session_id": "sess-001",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 1,
        "attempts": [{
            "attempt_index": 1,
            "failed_cases": [{"id": "case.fail", "status": "fail", "failure_reason": "boom", "command": "echo boom"}],
            "evidence_path": str(tmp_path / "evidence_bundle.json"),
        }],
    }
    Path(tmp_path / "evidence_bundle.json").write_text(json.dumps({"evidence": {"dmesg": {"commands": ["dmesg"]}}}), encoding="utf-8")
    request_path = analyze_request_stage(session)
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    assert request["failed_cases"][0]["id"] == "case.fail"
    assert "dmesg" in request["collectors_output"]
```

- [ ] **Step 2: 运行阶段测试，确认先失败**

Run:
```bash
export PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python"
python3 -m pytest engineering/loop/controller/python/tests/test_stages.py -v
```

Expected:
- FAIL because `loop_controller.stages` does not exist.

- [ ] **Step 3: 新建 `stages.py`，提取 run/analyze/decide 纯函数**

```python
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import StageResult
from loop_controller.analyzer_protocol import AnalysisRequest


def run_verify_stage(session_path: str, suite: str, adb_endpoint: str) -> tuple[dict, StageResult]:
    session_data = _load_session(session_path)
    artifacts_dir = session_data["artifacts_dir"]
    attempt = session_data.get("current_attempt", 0) + 1
    cmd = [sys.executable, "-m", "loop_core.cli", "run", "--suite", suite, "--artifacts-dir", artifacts_dir, "--case-dirs", _CASES_DIR, "--device-profile", _DEVICE_PROFILE]
    if adb_endpoint:
        cmd += ["--adb-endpoint", adb_endpoint]
    rc = subprocess.run(cmd, capture_output=False, env=_build_env()).returncode
    bundle_path = Path(artifacts_dir) / "evidence_bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8")) if bundle_path.exists() else {}
    status = "PASS" if rc == 0 else "FAIL"
    session_data["current_attempt"] = attempt
    session_data["status"] = status
    session_data.setdefault("attempts", []).append({
        "attempt_index": attempt,
        "verify_result": status,
        "evidence_path": str(bundle_path) if bundle_path.exists() else "",
        "failed_cases": _extract_failed_cases(bundle),
        "failure_code": "" if status == "PASS" else FailureCode.RUN_FAILED.value,
    })
    return session_data, StageResult(stage_name="RUN_VERIFY", status=status, failure_code=FailureCode.NONE if status == "PASS" else FailureCode.RUN_FAILED)


def analyze_request_stage(session_data: dict) -> str:
    artifacts_dir = session_data["artifacts_dir"]
    last = session_data.get("attempts", [])[-1]
    evidence_path = last.get("evidence_path", "")
    bundle = json.loads(Path(evidence_path).read_text(encoding="utf-8")) if evidence_path and Path(evidence_path).exists() else {}
    request = AnalysisRequest(
        session_id=session_data.get("session_id", ""),
        attempt_index=session_data.get("current_attempt", 0),
        failed_cases=last.get("failed_cases", []),
        evidence_bundle_path=evidence_path,
        collectors_output=bundle.get("evidence", {}),
        workspace_diff_so_far=_get_workspace_diff(),
    )
    req_path = Path(artifacts_dir) / "analysis_request.json"
    req_path.write_text(json.dumps(request.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return str(req_path)


def decide_stage(session_data: dict) -> dict[str, object]:
    attempts = session_data.get("attempts", [])
    last = attempts[-1] if attempts else {}
    current_hash = last.get("patch_applied", {}).get("patch_hash", "")
    if current_hash and any(att.get("patch_applied", {}).get("patch_hash", "") == current_hash for att in attempts[:-1]):
        return {"decision": "STOP", "reason": "duplicate_patch_detected", "should_escalate": True, "failure_code": FailureCode.DUPLICATE_PATCH.value}
    return _decide_with_policy(session_data)
```

- [ ] **Step 4: 修改 `control_cli.py`，让旧入口委托给 `stages.py`，避免重复逻辑继续膨胀**

```python
from loop_controller.stages import (
    analyze_request_stage,
    apply_patch_stage,
    compile_stage,
    decide_stage,
    deploy_stage,
    revert_stage,
    run_verify_stage,
)


def _handle_control_run_verify(args: argparse.Namespace) -> int:
    session_data, stage = run_verify_stage(args.session, args.suite, args.adb_endpoint)
    _save_session(session_data, session_data["artifacts_dir"])
    print(f"verify={stage.status} attempt={session_data['current_attempt']}")
    return 0 if stage.status == "PASS" else 1
```

- [ ] **Step 5: 运行阶段测试与旧 control_cli 测试，确认行为保持兼容**

Run:
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_stages.py engineering/loop/controller/python/tests/test_control_cli.py -v
```

Expected:
- PASS for new `test_stages.py`
- Existing `test_control_cli.py` keeps passing after delegation.

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/stages.py engineering/loop/controller/python/loop_controller/control_cli.py engineering/loop/controller/python/tests/test_stages.py engineering/loop/controller/python/tests/test_control_cli.py
git commit -m "refactor(loop-runtime): extract reusable stage handlers"
```

---

### Task 3: 新建 runtime 核心骨架（types/guards/checkpoint/engine）

**Files:**
- Create: `engineering/loop/controller/python/loop_controller/runtime/__init__.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/types.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/guards.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime/engine.py`
- Create: `engineering/loop/controller/python/tests/test_runtime_guards.py`
- Create: `engineering/loop/controller/python/tests/test_checkpoint_store.py`
- Create: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 先写 runtime guards 失败测试**

```python
from loop_controller.runtime.guards import (
    evaluate_guard,
    GuardEvalRequest,
)
from loop_contracts.failure_codes import FailureCode


def test_guard_all_cases_passed():
    result = evaluate_guard(GuardEvalRequest(
        guard_name="all_cases_passed",
        attempt_count=1,
        max_attempts=5,
        latest_status="PASS",
        latest_failure_code=FailureCode.NONE,
        previous_failure_codes=[],
        current_patch_hash="",
        previous_patch_hashes=[],
    ))
    assert result.matched is True
    assert result.next_node == "DONE_SUCCESS"


def test_guard_attempt_limit_reached():
    result = evaluate_guard(GuardEvalRequest(
        guard_name="attempt_limit_reached",
        attempt_count=6,
        max_attempts=5,
        latest_status="FAIL",
        latest_failure_code=FailureCode.RUN_FAILED,
        previous_failure_codes=[FailureCode.RUN_FAILED, FailureCode.COMPILE_FAILED],
        current_patch_hash="",
        previous_patch_hashes=[],
    ))
    assert result.matched is True
    assert result.next_node == "ESCALATE_HUMAN"


def test_guard_repeated_failure_code():
    result = evaluate_guard(GuardEvalRequest(
        guard_name="repeated_failure_code",
        attempt_count=2,
        max_attempts=5,
        latest_status="FAIL",
        latest_failure_code=FailureCode.RUN_FAILED,
        previous_failure_codes=[FailureCode.RUN_FAILED],
        current_patch_hash="",
        previous_patch_hashes=[],
    ))
    assert result.matched is True


def test_guard_duplicate_patch_hash():
    result = evaluate_guard(GuardEvalRequest(
        guard_name="duplicate_patch_hash",
        attempt_count=2,
        max_attempts=5,
        latest_status="FAIL",
        latest_failure_code=FailureCode.RUN_FAILED,
        previous_failure_codes=[],
        current_patch_hash="abc123",
        previous_patch_hashes=["abc123"],
    ))
    assert result.matched is True


def test_guard_attempts_below_limit():
    result = evaluate_guard(GuardEvalRequest(
        guard_name="attempts_below_limit",
        attempt_count=2,
        max_attempts=5,
        latest_status="FAIL",
        latest_failure_code=FailureCode.RUN_FAILED,
        previous_failure_codes=[],
        current_patch_hash="",
        previous_patch_hashes=[],
    ))
    assert result.matched is True
    assert result.next_node == "BUILD_ANALYSIS_REQUEST"


def test_guard_deploy_success_and_verify_passed():
    result = evaluate_guard(GuardEvalRequest(
        guard_name="deploy_success_and_verify_passed",
        attempt_count=1,
        max_attempts=5,
        latest_status="PASS",
        latest_failure_code=FailureCode.NONE,
        previous_failure_codes=[],
        current_patch_hash="",
        previous_patch_hashes=[],
    ))
    assert result.matched is True
```

- [ ] **Step 2: 运行 guards 测试，确认先失败**

Run:
```bash
export PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_guards.py -v
```

Expected: FAIL because `loop_controller.runtime.guards` does not exist.

- [ ] **Step 3: 创建 `runtime/__init__.py` 与 `runtime/types.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from loop_contracts.failure_codes import FailureCode


class NodeKind(StrEnum):
    INIT_SESSION = "INIT_SESSION"
    RUN_VERIFY = "RUN_VERIFY"
    DECIDE_NEXT = "DECIDE_NEXT"
    BUILD_ANALYSIS_REQUEST = "BUILD_ANALYSIS_REQUEST"
    WAIT_ANALYZER_PATCH = "WAIT_ANALYZER_PATCH"
    APPLY_PATCH = "APPLY_PATCH"
    COMPILE_PATCH = "COMPILE_PATCH"
    DEPLOY_PATCH = "DEPLOY_PATCH"
    REVERT_PATCH = "REVERT_PATCH"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    DONE_SUCCESS = "DONE_SUCCESS"
    DONE_FAILURE = "DONE_FAILURE"


@dataclass
class NodeResult:
    node: str
    status: str  # PASS / FAIL / PENDING_HUMAN
    failure_code: FailureCode = FailureCode.NONE
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionDecision:
    from_node: str
    to_node: str
    matched_guards: list[str]
    reason_summary: str
    should_escalate: bool = False


@dataclass
class GuardEvalRequest:
    guard_name: str
    attempt_count: int
    max_attempts: int
    latest_status: str
    latest_failure_code: FailureCode
    previous_failure_codes: list[FailureCode]
    current_patch_hash: str
    previous_patch_hashes: list[str]


@dataclass
class GuardEvalResult:
    matched: bool
    next_node: str = ""
    reason: str = ""
```

- [ ] **Step 4: 创建 `runtime/guards.py`**

```python
from __future__ import annotations

from loop_controller.runtime.types import GuardEvalRequest, GuardEvalResult, NodeKind
from loop_contracts.failure_codes import FailureCode

_GUARD_REGISTRY: dict[str, callable] = {}


def _register(name: str):
    def deco(fn):
        _GUARD_REGISTRY[name] = fn
        return fn
    return deco


@_register("all_cases_passed")
def _guard_all_cases_passed(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_status == "PASS" and req.latest_failure_code == FailureCode.NONE:
        return GuardEvalResult(matched=True, next_node=NodeKind.DONE_SUCCESS.value, reason="verification passed")
    return GuardEvalResult(matched=False)


@_register("attempt_limit_reached")
def _guard_attempt_limit_reached(req: GuardEvalRequest) -> GuardEvalResult:
    if req.attempt_count >= req.max_attempts:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="max attempts exceeded")
    return GuardEvalResult(matched=False)


@_register("repeated_failure_code")
def _guard_repeated_failure_code(req: GuardEvalRequest) -> GuardEvalResult:
    if req.previous_failure_codes and req.latest_failure_code == req.previous_failure_codes[-1]:
        if req.latest_failure_code != FailureCode.NONE:
            return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="same failure repeated")
    return GuardEvalResult(matched=False)


@_register("duplicate_patch_hash")
def _guard_duplicate_patch_hash(req: GuardEvalRequest) -> GuardEvalResult:
    if req.current_patch_hash and req.previous_patch_hashes and req.current_patch_hash in req.previous_patch_hashes:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="duplicate patch detected")
    return GuardEvalResult(matched=False)


@_register("attempts_below_limit")
def _guard_attempts_below_limit(req: GuardEvalRequest) -> GuardEvalResult:
    if req.attempt_count < req.max_attempts:
        return GuardEvalResult(matched=True, next_node=NodeKind.BUILD_ANALYSIS_REQUEST.value, reason="retry allowed")
    return GuardEvalResult(matched=False)


@_register("deploy_success_and_verify_passed")
def _guard_deploy_success_and_verify_passed(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_status == "PASS" and req.latest_failure_code == FailureCode.NONE:
        return GuardEvalResult(matched=True, next_node=NodeKind.DONE_SUCCESS.value, reason="deploy and verify passed")
    return GuardEvalResult(matched=False)


@_register("patch_rejected")
def _guard_patch_rejected(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.PATCH_REJECTED:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="patch rejected by guard")
    return GuardEvalResult(matched=False)


@_register("compile_failed_but_recoverable")
def _guard_compile_failed_but_recoverable(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.COMPILE_FAILED:
        return GuardEvalResult(matched=True, next_node=NodeKind.REVERT_PATCH.value, reason="compile failed, revert")
    return GuardEvalResult(matched=False)


@_register("patch_applied_successfully")
def _guard_patch_applied_successfully(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_status == "APPLIED":
        return GuardEvalResult(matched=True, next_node=NodeKind.COMPILE_PATCH.value, reason="patch applied, compile")
    return GuardEvalResult(matched=False)


@_register("kernel_dead_no_shell")
def _guard_kernel_dead_no_shell(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.KERNEL_DEAD_NO_SHELL:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="kernel dead, no serial shell")
    return GuardEvalResult(matched=False)


@_register("deploy_failed_but_recoverable")
def _guard_deploy_failed_but_recoverable(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.DEPLOY_FATAL:
        return GuardEvalResult(matched=True, next_node=NodeKind.DECIDE_NEXT.value, reason="deploy failed, back to decide")
    return GuardEvalResult(matched=False)


def evaluate_guard(req: GuardEvalRequest) -> GuardEvalResult:
    handler = _GUARD_REGISTRY.get(req.guard_name)
    if handler is None:
        return GuardEvalResult(matched=False, reason=f"unknown guard: {req.guard_name}")
    return handler(req)


def guard_chain(guard_names: list[str], req: GuardEvalRequest) -> GuardEvalResult:
    for name in guard_names:
        result = evaluate_guard(GuardEvalRequest(
            guard_name=name,
            attempt_count=req.attempt_count,
            max_attempts=req.max_attempts,
            latest_status=req.latest_status,
            latest_failure_code=req.latest_failure_code,
            previous_failure_codes=req.previous_failure_codes,
            current_patch_hash=req.current_patch_hash,
            previous_patch_hashes=req.previous_patch_hashes,
        ))
        if result.matched:
            return result
    return GuardEvalResult(matched=False)
```

- [ ] **Step 5: 运行 guards 测试，确认 PASS**

Run:
```bash
export PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_guards.py -v
```

Expected: PASS.

- [ ] **Step 6: 先写 checkpoint_store 测试**

```python
import json
from pathlib import Path
from loop_controller.runtime.checkpoint_store import CheckpointStore
from loop_contracts.models import CheckpointRecord
from loop_contracts.failure_codes import FailureCode


def test_checkpoint_store_save_and_load(tmp_path: Path):
    store = CheckpointStore(str(tmp_path), "sess-001")
    cp = CheckpointRecord(
        checkpoint_id="cp-001", session_id="sess-001", attempt_index=1,
        current_node="RUN_VERIFY",
        input_summary={"suite": "t.yaml"},
        output_summary={"verify_result": "FAIL"},
        failure_code=FailureCode.RUN_FAILED,
        matched_guards=["attempts_below_limit"],
        next_node="BUILD_ANALYSIS_REQUEST",
        timestamp="2026-06-26T12:00:00+08:00",
    )
    store.save(cp)
    loaded = store.latest()
    assert loaded is not None
    assert loaded.checkpoint_id == "cp-001"
    assert loaded.next_node == "BUILD_ANALYSIS_REQUEST"


def test_checkpoint_store_returns_none_when_empty(tmp_path: Path):
    store = CheckpointStore(str(tmp_path), "sess-none")
    assert store.latest() is None
```

- [ ] **Step 7: 运行 checkpoint_store 测试，确认先失败**

Run:
```bash
export PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python"
python3 -m pytest engineering/loop/controller/python/tests/test_checkpoint_store.py -v
```

Expected: FAIL.

- [ ] **Step 8: 创建 `runtime/checkpoint_store.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

from loop_contracts.models import CheckpointRecord

_CHECKPOINT_FILENAME = "runtime_checkpoints.jsonl"


class CheckpointStore:
    def __init__(self, artifacts_dir: str, session_id: str) -> None:
        self._path = Path(artifacts_dir) / _CHECKPOINT_FILENAME
        self._session_id = session_id

    def save(self, cp: CheckpointRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(cp.to_dict(), ensure_ascii=False) + "\n")

    def latest(self) -> CheckpointRecord | None:
        if not self._path.exists():
            return None
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return None
        return self._from_line(lines[-1])

    def all(self) -> list[CheckpointRecord]:
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        return [self._from_line(line) for line in lines if line]

    def _from_line(self, line: str) -> CheckpointRecord:
        from loop_contracts.failure_codes import FailureCode
        data = json.loads(line)
        failure_code_value = data.get("failure_code", "NONE")
        return CheckpointRecord(
            checkpoint_id=data["checkpoint_id"],
            session_id=data["session_id"],
            attempt_index=data["attempt_index"],
            current_node=data["current_node"],
            input_summary=data.get("input_summary", {}),
            output_summary=data.get("output_summary", {}),
            failure_code=FailureCode(failure_code_value),
            matched_guards=data.get("matched_guards", []),
            next_node=data["next_node"],
            timestamp=data["timestamp"],
        )
```

- [ ] **Step 9: 运行 checkpoint_store 测试，确认 PASS**

Run:
```bash
export PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python"
python3 -m pytest engineering/loop/controller/python/tests/test_checkpoint_store.py -v
```

Expected: PASS.

- [ ] **Step 10: 先写 runtime engine 测试**

```python
import json
from pathlib import Path
from loop_controller.runtime.engine import LoopRuntime
from loop_contracts.models import LoopSession, RuntimeState, RuntimeTerminalState

_LOOP_CASES_DIR = "engineering/loop/cases"
_DEVICE_PROFILE = "engineering/loop/connection/profiles/devices/rp5/adb.json"


def test_runtime_init_session(tmp_path: Path, monkeypatch):
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    Path(tmp_path / "evidence_bundle.json").write_text(json.dumps({
        "summary": {"overall": "PASS", "total": 1, "passed": 1, "failed": 0, "skipped": 0},
        "cases": [],
    }), encoding="utf-8")

    session = LoopSession(
        session_id="sess-001", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, _LOOP_CASES_DIR, _DEVICE_PROFILE)
    result = rt.run()
    assert result.terminal_state == RuntimeTerminalState.DONE_SUCCESS


def test_runtime_escalates_on_max_attempts(tmp_path: Path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(" ".join(cmd[:4]))
        class R:
            returncode = 1
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    Path(tmp_path / "evidence_bundle.json").write_text(json.dumps({
        "summary": {"overall": "FAIL", "total": 1, "passed": 0, "failed": 1, "skipped": 0},
        "cases": [{"id": "case.fail", "status": "fail", "failure_reason": "boom", "command": "echo boom"}],
    }), encoding="utf-8")

    session = LoopSession(
        session_id="sess-002", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=1, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, _LOOP_CASES_DIR, _DEVICE_PROFILE)
    result = rt.run()
    assert result.terminal_state == RuntimeTerminalState.ESCALATE_HUMAN
```

- [ ] **Step 11: 运行 engine 测试，确认先失败**

Run:
```bash
export PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/core/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v
```

Expected: FAIL.

- [ ] **Step 12: 创建 `runtime/engine.py`**

```python
from __future__ import annotations

import time
import uuid
from copy import deepcopy

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import (
    CheckpointRecord,
    LoopSession,
    RuntimeState,
    RuntimeTerminalState,
)
from loop_controller.runtime.types import NodeKind, GuardEvalRequest
from loop_controller.runtime.guards import guard_chain
from loop_controller.runtime.checkpoint_store import CheckpointStore

import loop_controller.stages as stages

_NODE_TRANSITIONS: dict[str, dict[str, object]] = {
    NodeKind.INIT_SESSION.value: {
        "next": NodeKind.RUN_VERIFY.value,
    },
    NodeKind.RUN_VERIFY.value: {
        "next": NodeKind.DECIDE_NEXT.value,
        "on_guard": "decide_guards",
    },
    NodeKind.DECIDE_NEXT.value: {
        "guards": ["all_cases_passed", "attempt_limit_reached", "repeated_failure_code", "duplicate_patch_hash"],
        "retry_guard": "attempts_below_limit",
    },
    NodeKind.BUILD_ANALYSIS_REQUEST.value: {
        "next": NodeKind.WAIT_ANALYZER_PATCH.value,
    },
    NodeKind.WAIT_ANALYZER_PATCH.value: {
        "next": NodeKind.APPLY_PATCH.value,
    },
    NodeKind.APPLY_PATCH.value: {
        "guards": ["patch_rejected"],
        "next": NodeKind.COMPILE_PATCH.value,
    },
    NodeKind.COMPILE_PATCH.value: {
        "guards": ["compile_failed_but_recoverable"],
        "next": NodeKind.DEPLOY_PATCH.value,
    },
    NodeKind.DEPLOY_PATCH.value: {
        "guards": ["kernel_dead_no_shell", "deploy_failed_but_recoverable"],
        "next": NodeKind.RUN_VERIFY.value,
    },
    NodeKind.REVERT_PATCH.value: {
        "next": NodeKind.DECIDE_NEXT.value,
    },
}


class LoopRuntime:
    def __init__(self, session: LoopSession, cases_dir: str, device_profile: str) -> None:
        self._session = session
        self._cases_dir = cases_dir
        self._device_profile = device_profile
        self._state = RuntimeState(current_node=NodeKind.INIT_SESSION.value)
        self._store = CheckpointStore(session.artifacts_dir, session.session_id)
        stages._CASES_DIR = cases_dir
        stages._DEVICE_PROFILE = device_profile

    def resume(self) -> RuntimeState:
        cp = self._store.latest()
        if cp:
            self._state.current_node = cp.next_node
            self._state.previous_node = cp.current_node
            self._state.interrupted = False
            self._state.last_checkpoint_at = cp.timestamp
        return self._state

    def run(self) -> RuntimeState:
        while self._state.terminal_state == RuntimeTerminalState.NONE:
            self._execute_current_node()
            if self._state.terminal_state != RuntimeTerminalState.NONE:
                break
            self._transition()
        return self._state

    def _execute_current_node(self) -> None:
        node = self._state.current_node
        if node == NodeKind.INIT_SESSION.value:
            self._state.node_status = "INITIALIZED"
            self._checkpoint("session initialized", FailureCode.NONE)
        elif node == NodeKind.RUN_VERIFY.value:
            session_dict = {
                "session_id": self._session.session_id,
                "artifacts_dir": self._session.artifacts_dir,
                "current_attempt": self._session.current_attempt,
                "max_attempts": self._session.max_attempts,
                "attempts": [deepcopy(a) for a in self._session.attempts],
                "status": self._session.status,
            }
            session_path = Path(self._session.artifacts_dir) / "session.json"
            session_path.write_text(json.dumps(session_dict), encoding="utf-8")
            updated, stage_result = stages.run_verify_stage(
                str(session_path), self._session.suite, ""
            )
            self._session.current_attempt = updated["current_attempt"]
            self._session.status = updated["status"]
            self._session.attempts = updated["attempts"]
            self._session.latest_failure_code = FailureCode(stage_result.failure_code.value)
            self._state.node_status = stage_result.status
            self._checkpoint(f"verify {stage_result.status}", stage_result.failure_code)
        elif node == NodeKind.DECIDE_NEXT.value:
            session_dict = self._to_session_dict()
            decision = stages.decide_stage(session_dict)
            self._state.transition_reason = str(decision.get("reason", ""))
            should_escalate = decision.get("should_escalate", False)
            if decision["decision"] == "STOP":
                if should_escalate:
                    self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                else:
                    self._state.terminal_state = RuntimeTerminalState.DONE_SUCCESS
            else:
                self._state.node_status = "RETRY"
            fc = FailureCode(decision.get("failure_code", FailureCode.NONE.value))
            self._checkpoint(f"decide={decision['decision']}", fc)
        elif node == NodeKind.BUILD_ANALYSIS_REQUEST.value:
            session_dict = self._to_session_dict()
            stages.analyze_request_stage(session_dict)
            self._state.node_status = "ANALYSIS_READY"
            self._checkpoint("analysis_request written", FailureCode.NONE)
        elif node == NodeKind.WAIT_ANALYZER_PATCH.value:
            self._state.node_status = "WAITING_PATCH"
            self._state.pending_human_gate = True
            self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
            self._checkpoint("waiting for analyzer patch", FailureCode.NONE)
        elif node == NodeKind.APPLY_PATCH.value:
            pass
        elif node == NodeKind.COMPILE_PATCH.value:
            pass
        elif node == NodeKind.DEPLOY_PATCH.value:
            pass
        elif node == NodeKind.REVERT_PATCH.value:
            pass

    def _transition(self) -> None:
        node = self._state.current_node
        config = _NODE_TRANSITIONS.get(node, {})
        next_node = config.get("next", "")
        if next_node:
            self._state.previous_node = self._state.current_node
            self._state.current_node = next_node

    def _checkpoint(self, reason: str, failure_code: FailureCode) -> None:
        cp = CheckpointRecord(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:12]}",
            session_id=self._session.session_id,
            attempt_index=self._session.current_attempt,
            current_node=self._state.current_node,
            input_summary={"suite": self._session.suite},
            output_summary={"node_status": self._state.node_status},
            failure_code=failure_code,
            matched_guards=[],
            next_node=next(iter(_NODE_TRANSITIONS.get(self._state.current_node, {}).get("next", [])), ""),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        )
        self._store.save(cp)
        self._state.last_checkpoint_at = cp.timestamp

    def _to_session_dict(self) -> dict:
        return {
            "session_id": self._session.session_id,
            "artifacts_dir": self._session.artifacts_dir,
            "current_attempt": self._session.current_attempt,
            "max_attempts": self._session.max_attempts,
            "attempts": [deepcopy(a) for a in self._session.attempts],
            "status": self._session.status,
        }
```

- [ ] **Step 13: 运行 engine 测试，确认 PASS**

Run:
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v
```

Expected: PASS (init_session → DONE_SUCCESS path).

- [ ] **Step 14: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/ engineering/loop/controller/python/tests/test_runtime_guards.py engineering/loop/controller/python/tests/test_checkpoint_store.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "feat(loop-runtime): add runtime core (types/guards/checkpoint/engine)"
```

---

### Task 4: runtime nodes 实现 + runtime CLI 入口

**Files:**
- Create: `engineering/loop/controller/python/loop_controller/runtime/nodes.py`
- Create: `engineering/loop/controller/python/loop_controller/runtime_cli.py`
- Modify: `engineering/loop/core/python/loop_core/cli.py`
- Modify: `engineering/loop/scripts/le.sh`
- Create: `engineering/loop/controller/python/tests/test_runtime_cli.py`

- [ ] **Step 1: 先写 runtime_cli 失败测试**

```python
import json
from pathlib import Path
from loop_controller.runtime_cli import main as runtime_main


def test_runtime_init_produces_session(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture_cli(runtime_main, [
        "init", "--target", "lciod",
        "--suite", "engineering/loop/cases/features/lciod/hal.yaml",
        "--max-attempts", "3",
        "--artifacts-dir", str(artifacts),
    ])
    assert rc == 0
    assert "session_id=" in out


def test_runtime_run_pass_smoke(tmp_path: Path, monkeypatch):
    """Runtime run 在 PASS 闭环下自动结束为 DONE_SUCCESS。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    # 先 init
    r, out = _capture_cli(runtime_main, [
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "3", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_session_id(out)
    assert sid

    # monkeypatch subprocess.run to simulate pass
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    Path(artifacts / "evidence_bundle.json").write_text(json.dumps({
        "summary": {"overall": "PASS", "total": 1, "passed": 1, "failed": 0, "skipped": 0},
        "cases": [],
    }), encoding="utf-8")

    rc, out = _capture_cli(runtime_main, [
        "run", "--session", str(artifacts / f"{sid}.json"),
    ])
    assert rc == 0
    assert "DONE_SUCCESS" in out or "terminal_state=DONE_SUCCESS" in out


def test_runtime_resume_restores_from_checkpoint(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    r, out = _capture_cli(runtime_main, [
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "3", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_session_id(out)
    rc, out = _capture_cli(runtime_main, [
        "resume", "--session", str(artifacts / f"{sid}.json"),
    ])
    assert rc == 0


def _capture_cli(main_fn, argv):
    import io, sys
    captured = io.StringIO()
    old = sys.stdout
    sys.stdout = captured
    try:
        rc = main_fn(argv)
    finally:
        sys.stdout = old
    return rc, captured.getvalue()


def _extract_session_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("session_id="):
            return line.split("=", 1)[1].strip()
    return ""
```

- [ ] **Step 2: 运行 runtime_cli 测试，确认先失败**

Run:
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py -v
```

Expected: FAIL because `loop_controller.runtime_cli` does not exist.

- [ ] **Step 3: 创建 `runtime/nodes.py`——实现 compile/deploy/revert/apply_patch 节点的 handler**

```python
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_controller.analyzer_protocol import FileChange
from loop_controller.patch_guard import check_white_list, detect_risk, check_syntax
from loop_controller.patch_applier import apply_file_changes


def node_apply_patch(patch_path: str, session_dict: dict, workspace_root: str) -> dict:
    target = session_dict.get("target", "")
    patch = json.loads(Path(patch_path).read_text(encoding="utf-8"))
    changes = [FileChange(**c) for c in patch]
    # white list guard
    allowed = _load_target_paths(target)
    guard = check_white_list(changes, allowed)
    if not guard.allowed:
        return {"status": "PATCH_REJECTED", "failure_code": FailureCode.PATCH_REJECTED, "error": f"rejected: {guard.rejected_files}"}
    # syntax guard
    syntax = check_syntax(changes, workspace_root)
    if syntax:
        return {"status": "SYNTAX_ERROR", "failure_code": FailureCode.PATCH_REJECTED, "error": syntax[0][:300]}
    # stash backup
    stash = subprocess.run(["git", "stash", "create", "-u"], capture_output=True, text=True, timeout=10, cwd=workspace_root)
    stash_ref = stash.stdout.strip() or ""
    # apply
    result = apply_file_changes(changes, workspace_root)
    risk = detect_risk(changes)
    patch_hash = hashlib.sha256(json.dumps(patch, sort_keys=True).encode()).hexdigest()
    if not result.success:
        if stash_ref:
            subprocess.run(["git", "stash", "apply", stash_ref], capture_output=True, text=True, timeout=10, cwd=workspace_root)
        return {"status": "APPLY_FAILED", "failure_code": FailureCode.PATCH_REJECTED, "error": result.error}
    return {
        "status": "APPLIED",
        "failure_code": FailureCode.NONE,
        "files": result.applied_files,
        "stash_ref": stash_ref,
        "patch_hash": patch_hash,
        "risk": risk,
        "workspace_root": workspace_root,
    }


def node_compile(session_dict: dict, workspace_root: str) -> dict:
    from loop_deploy.compiler import compile_plan
    from loop_deploy.decider import get_diff_files, decide
    from loop_deploy.models import DeployMode, DeployPlan
    try:
        diff_files = get_diff_files("HEAD")
    except RuntimeError as e:
        return {"status": "COMPILE_FAILED", "failure_code": FailureCode.COMPILE_FAILED, "error": str(e)}
    plan = decide(diff_files)
    if plan.mode == DeployMode.SKIP and diff_files:
        has_code = any(Path(f).suffix.lower() in {".cpp", ".c", ".cc", ".h", ".hpp", ".bp", ".java", ".kt"} for f in diff_files)
        if has_code:
            plan = DeployPlan(mode=DeployMode.PUSH_SINGLE, changed_files=diff_files, reason="manual compile", build_targets=[], deploy_targets=[], requires_reboot=False, estimated_seconds=600)
    result = compile_plan(plan, workspace_root)
    if result.success:
        return {"status": "COMPILED", "failure_code": FailureCode.NONE, "artifacts": result.artifacts}
    return {"status": "COMPILE_FAILED", "failure_code": FailureCode.COMPILE_FAILED, "error": result.error}


def node_revert(session_dict: dict) -> dict:
    attempts = session_dict.get("attempts", [])
    for att in reversed(attempts):
        patch_applied = att.get("patch_applied", {})
        stash_ref = patch_applied.get("stash_ref", "")
        if stash_ref:
            ws = patch_applied.get("workspace_root", os.environ.get("AOSP_ROOT", os.path.expanduser("~/workspace/aosp")))
            r = subprocess.run(["git", "stash", "apply", stash_ref], capture_output=True, text=True, timeout=30, cwd=ws)
            if r.returncode == 0:
                return {"status": "REVERTED", "failure_code": FailureCode.NONE}
            return {"status": "REVERT_FAILED", "failure_code": FailureCode.ROLLBACK_FAILED, "error": r.stderr[:300]}
    return {"status": "NO_STASH_REF", "failure_code": FailureCode.ROLLBACK_FAILED, "error": "no stash ref found"}


def _load_target_paths(target: str) -> list[str]:
    import yaml
    try:
        from harness_path_util import path
        yaml_path = str(path("LOOP_DIR") / "config" / "target-paths.yaml")
    except Exception:
        yaml_path = str(Path(__file__).resolve().parent.parent.parent.parent / "config" / "target-paths.yaml")
    p = Path(yaml_path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return list(data.get(target, []))
```

- [ ] **Step 4: 创建 `runtime_cli.py`**

```python
"""runtime_cli：新 runtime 主入口——le runtime {init,run,resume,status,explain}。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from loop_contracts.models import LoopSession, RuntimeState, RuntimeTerminalState
from loop_controller.runtime.engine import LoopRuntime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Loop Runtime CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init_p = sub.add_parser("init", help="initialize loop session")
    init_p.add_argument("--target", required=True)
    init_p.add_argument("--suite", required=True)
    init_p.add_argument("--max-attempts", type=int, default=5)
    init_p.add_argument("--artifacts-dir", required=True)
    init_p.set_defaults(func=_handle_init)

    run_p = sub.add_parser("run", help="execute full auto-loop")
    run_p.add_argument("--session", required=True)
    run_p.add_argument("--adb-endpoint", default="")
    run_p.set_defaults(func=_handle_run)

    resume_p = sub.add_parser("resume", help="resume from last checkpoint")
    resume_p.add_argument("--session", required=True)
    resume_p.set_defaults(func=_handle_resume)

    status_p = sub.add_parser("status", help="show session state and last checkpoint")
    status_p.add_argument("--session", required=True)
    status_p.set_defaults(func=_handle_status)

    explain_p = sub.add_parser("explain", help="explain what the runtime will do next")
    explain_p.add_argument("--session", required=True)
    explain_p.set_defaults(func=_handle_explain)

    args = parser.parse_args(argv)
    return args.func(args)


def _resolve_paths():
    try:
        from harness_path_util import path
        return str(path("LOOP_CASES_DIR")), str(path("LOOP_DIR") / "connection" / "profiles" / "devices" / "rp5" / "adb.json")
    except Exception:
        loop_dir = Path(__file__).resolve().parent.parent.parent.parent
        return str(loop_dir / "cases"), str(loop_dir / "connection" / "profiles" / "devices" / "rp5" / "adb.json")

_CASES_DIR, _DEVICE_PROFILE = _resolve_paths()


def _handle_init(args: argparse.Namespace) -> int:
    sid = f"{args.target}-{time.strftime('%Y%m%d%H%M%S')}"
    session = LoopSession(
        session_id=sid, workflow_id="runtime", target=args.target,
        suite=args.suite, max_attempts=args.max_attempts,
        artifacts_dir=args.artifacts_dir,
    )
    out_path = Path(args.artifacts_dir) / f"{sid}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_session_to_dict(session), indent=2, ensure_ascii=False), encoding="utf-8")
    latest = Path(args.artifacts_dir) / "session.json"
    latest.write_text(json.dumps(_session_to_dict(session), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"session_id={sid}")
    print(f"artifacts_dir={args.artifacts_dir}")
    print(f"session_path={out_path}")
    return 0


def _handle_run(args: argparse.Namespace) -> int:
    session = _load_session(args.session)
    rt = LoopRuntime(session, _CASES_DIR, _DEVICE_PROFILE)
    state = rt.run()
    print(f"terminal_state={state.terminal_state.value}")
    if args.adb_endpoint:
        print(f"adb_endpoint={args.adb_endpoint}")
    return 0 if state.terminal_state == RuntimeTerminalState.DONE_SUCCESS else 1


def _handle_resume(args: argparse.Namespace) -> int:
    session = _load_session(args.session)
    rt = LoopRuntime(session, _CASES_DIR, _DEVICE_PROFILE)
    state = rt.resume()
    print(f"resumed to node={state.current_node} terminal={state.terminal_state.value}")
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    session = _load_session(args.session)
    print(json.dumps(_session_to_dict(session), indent=2, ensure_ascii=False))
    return 0


def _handle_explain(args: argparse.Namespace) -> int:
    print("Runtime will execute: INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT")
    print("On PASS: DONE_SUCCESS. On FAIL: analyze/patch/compile/deploy/retry.")
    print("On attempt>=max: ESCALATE_HUMAN.")
    return 0


def _load_session(path_str: str) -> LoopSession:
    from loop_contracts.failure_codes import FailureCode
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return LoopSession(
        session_id=data.get("session_id", ""),
        workflow_id=data.get("workflow_id", "runtime"),
        target=data.get("target", ""),
        suite=data.get("suite", ""),
        max_attempts=data.get("max_attempts", 5),
        current_attempt=data.get("current_attempt", 0),
        status=data.get("status", "PENDING"),
        latest_failure_code=FailureCode(data.get("latest_failure_code", "NONE")),
        attempts=data.get("attempts", []),
        artifacts_dir=data.get("artifacts_dir", ""),
    )


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
    }
```

- [ ] **Step 5: 在 `loop_core/cli.py` 挂载新 runtime 入口**

在 `def main(argv)` 内最后 try/except 块之后，追加 runtime 子命令注册：

```python
    try:
        from loop_controller.runtime_cli import main as _runtime_main
        # 不在此处注册，runtime 入口独立；此处预留让 loop_core.cli 知道 runtime 存在
    except ImportError:
        pass
```

- [ ] **Step 6: 在 `le.sh` 增加 `runtime` 顶层命令**

```bash
runtime)
    shift
    PYTHONPATH="$PYTHONPATH" python3 -m loop_controller.runtime_cli "$@"
    ;;
```

- [ ] **Step 7: 运行 runtime_cli 测试，确认 PASS**

Run:
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py -v
```

Expected: PASS.

- [ ] **Step 8: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/nodes.py engineering/loop/controller/python/loop_controller/runtime_cli.py engineering/loop/core/python/loop_core/cli.py engineering/loop/scripts/le.sh engineering/loop/controller/python/tests/test_runtime_cli.py
git commit -m "feat(loop-runtime): add runtime nodes and CLI entry point"
```

---

### Task 5: 能力层提纯——evidence/runner/connection 接口标准化

**Files:**
- Modify: `engineering/loop/core/python/loop_core/evidence.py`
- Modify: `engineering/loop/core/python/loop_core/runner.py`
- Modify: `engineering/loop/core/python/loop_core/cli.py`
- Modify: `engineering/loop/deploy/python/loop_deploy/cli.py`
- Modify: `engineering/loop/deploy/python/loop_deploy/deployer.py`
- Modify: `engineering/loop/deploy/python/loop_deploy/compiler.py`
- Modify: `engineering/loop/deploy/python/loop_deploy/rollback.py`
- Modify: `engineering/loop/connection/README.md`

- [ ] **Step 1: 确保 `evidence.write_evidence_bundle` 返回稳定路径结构**

检查并增强 `evidence.py`，确保 `write_evidence_bundle` 总是返回 `{"evidence_json": <path>, "summary_txt": <path>}`，且在 bundle 中含 `execution_config` 摘要。

- [ ] **Step 2: 确保 `runner.LoopRunner.run()` 总是返回 `EvidenceBundle` 且不被 exception 吞没**

检查 runner/CLI 已有顶层兜底：`engineering/loop/core/python/loop_core/cli.py:177-179`。确认新旧 runtime 始终拿到结构化证据。

- [ ] **Step 3: 确保 deploy 模块 `compile_plan/deploy/rollback` 接口统一且返回结构化结果**

检查 `compiler.py:compile_plan` / `deployer.py:deploy` / `rollback.py` 是否都返回含有 `success/failure_code/artifacts/error` 字段的结构。

- [ ] **Step 4: 更新 `connection/README.md` 注明 provider 与 runtime 边界**

```md
## Runtime 边界

connection providers 只负责传输与数据转发，不包含任何业务编排逻辑。
所有 verify → decide → analyze → patch → compile → deploy → rerun 编排均由 runtime 引擎驱动。
```

- [ ] **Step 5: 运行能力层全量测试**

Run:
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/core/python/tests/ engineering/loop/deploy/python/tests/ -v
```

Expected: all PASS.

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/core/python/loop_core/ evidence.py engineering/loop/core/python/loop_core/runner.py engineering/loop/deploy/python/loop_deploy/ engineering/loop/connection/README.md
git commit -m "refactor(loop-runtime): standardize capability module interfaces"
```

---

### Task 6: Legacy Removal——删除旧编排文件与旧测试

**Files:**
- Delete: `engineering/loop/controller/python/loop_controller/control_cli.py`
- Delete: `engineering/loop/controller/python/loop_controller/engine.py`
- Delete: `engineering/loop/controller/python/loop_controller/policy.py`
- Delete: `engineering/loop/controller/python/loop_controller/state.py`
- Delete: `engineering/loop/controller/python/tests/test_control_cli.py`
- Delete: `engineering/loop/controller/python/tests/test_engine.py`
- Delete: `engineering/loop/controller/python/tests/test_policy.py`
- Delete: `engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh`
- Modify: `engineering/loop/core/python/loop_core/cli.py`
- Modify: `engineering/loop/workflows/README.md`

- [ ] **Step 1: 在 `loop_core/cli.py` 中断开旧 `add_control_parser` 的挂载**

移除 try/except 中的旧 control parser 注册，只保留 runtime 入口。

```python
    # 移除旧 control 入口；由 runtime_cli 完全替代
    try:
        from loop_controller.runtime_cli import main as _runtime_main
    except ImportError:
        pass
```

- [ ] **Step 2: 按项目规则列出待删除文件并向用户确认**

将以下文件写入删除清单（本阶段结束前已预先在此计划中列出）：
1. `engineering/loop/controller/python/loop_controller/control_cli.py`
2. `engineering/loop/controller/python/loop_controller/engine.py`
3. `engineering/loop/controller/python/loop_controller/policy.py`
4. `engineering/loop/controller/python/loop_controller/state.py`
5. `engineering/loop/controller/python/tests/test_control_cli.py`
6. `engineering/loop/controller/python/tests/test_engine.py`
7. `engineering/loop/controller/python/tests/test_policy.py`
8. `engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh`

**用户确认后**再执行 git rm。

- [ ] **Step 3: 运行全量回归测试，确保旧删除不破坏新 runtime**

Run:
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/contracts/python/tests/ engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/deploy/python/tests/ -v
```

Expected: all PASS.

- [ ] **Step 4: 更新 `workflows/README.md` 反映旧编排已删除**

```md
# Loop Workflows

loop engineering 专属 workflow。旧 phase plan / bootstrap / fallback / rerun 编排已由 runtime 引擎完全替代。本目录当前仅保留 workflow 知识参考。
```

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/ engineering/loop/workflows/ engineering/loop/core/python/loop_core/cli.py
git commit -m "refactor(loop-runtime): remove legacy control/workflow orchestration"
```

---

### Task 7: 文档重写——WORKFLOW.md / README / controller README

**Files:**
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/controller/README.md`
- Modify: `engineering/loop/scripts/README.md`

- [ ] **Step 1: 重写 `WORKFLOW.md`**

新原文只需包含：

```md
# Loop Runtime Workflow

## Runtime 架构
Loop 自动化闭环由自研状态图 runtime 驱动。engine 会按照固定状态机 `INIT -> VERIFY -> DECIDE -> ANALYZE -> PATCH -> COMPILE -> DEPLOY -> RERUN` 执行，并在 `DONE_SUCCESS` 或 `ESCALATE_HUMAN` 处终止。

## 入口
- `le runtime init --target <t> --suite <s> --max-attempts <n> --artifacts-dir <d>`
- `le runtime run --session <path>`
- `le runtime resume --session <path>`

## 状态机
```text
INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT
  -> DONE_SUCCESS | BUILD_ANALYSIS_REQUEST | ESCALATE_HUMAN
BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH -> APPLY_PATCH
  -> COMPILE_PATCH -> DEPLOY_PATCH -> RUN_VERIFY
  -> REVERT_PATCH -> DECIDE_NEXT
```

## Guard 清单
| Guard | 类型 | 触发动作 |
|---|---|---|
| all_cases_passed | success | DONE_SUCCESS |
| attempt_limit_reached | terminal | ESCALATE_HUMAN |
| repeated_failure_code | terminal | ESCALATE_HUMAN |
| duplicate_patch_hash | terminal | ESCALATE_HUMAN |
| patch_rejected | terminal | ESCALATE_HUMAN |
| kernel_dead_no_shell | terminal | ESCALATE_HUMAN |
| attempts_below_limit | retry | BUILD_ANALYSIS_REQUEST |
| compile_failed_but_recoverable | retry | REVERT_PATCH |

## 人工 gate（除 FAIL>=5 外以下场景立即退人工）
1. kernel_dead_no_shell
2. PATCH_REJECTED 且属于越权/越界
3. SESSION_STATE_ERROR / checkpoint 损坏
4. deploy 回退失败
5. transport 不可恢复

## 部署硬规则
1. 能 PUSH_SINGLE 不 dd
2. dd 前四阶段防护网
3. kernel 死 escalate 人工

## 连接层（RPi5）
- serial 是唯一可信 IP 发现通道
- serial→adb 为硬前置依赖链
- 禁止硬编码设备 IP
- rp5-serial host/client 拓扑仍由 connection provider 提供

## AI 诊断约束
- 仅使用 evidence_bundle.json + analysis_request.json + serial_context
- 不强行单一根因
- 可并列多个候选修复方向
```

- [ ] **Step 2: 重写 `controller/README.md`**

```md
# Loop Controller — Runtime Control Center

controller 现为 loop 的控制面与 runtime 编排中心。

## 目录结构
| 子目录/文件 | 职责 |
|---|---|
| `runtime/` | 状态图 runtime（types/guards/engine/checkpoint_store/nodes） |
| `runtime_cli.py` | runtime CLI（le runtime init/run/resume/status/explain） |
| `stages.py` | 阶段 handler（verify/decide/analyze/apply_patch/compile/deploy/revert） |
| `patch_applier.py` / `patch_guard.py` | 补丁应用与防护 |
| `analyzer_protocol.py` | analyzer 输入输出契约 |

## 入口
新 runtime CLI 替代旧 control CLI，旧 le control 命令体系已删除。
```

- [ ] **Step 3: 更新 `loop/README.md` 和 `scripts/README.md`**

`loop/README.md` 在 "目录说明" 表中更新 controller 描述为 "controller（runtime 控制中心）"。
`scripts/README.md` 更新 `le.sh` 能力说明，增加 `le runtime` 入口。

- [ ] **Step 4: 全量回归测试**

Run:
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/contracts/python/tests/ engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/deploy/python/tests/ -v
```

Expected: all PASS.

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/WORKFLOW.md engineering/loop/README.md engineering/loop/controller/README.md engineering/loop/scripts/README.md
git commit -m "docs(loop-runtime): rewrite docs for new runtime architecture"
```

---

## 切换验证清单

在最终切换默认入口前，建议完成以下验收：

1. `le runtime init + run` 全 PASS 闭环至少验证 1 次（可用 fixtureTransport 离线跑）。
2. `le runtime init + run` 在 FAIL 场景下至少触发 1 次 `FAIL >= 5 -> ESCALATE_HUMAN`。
3. `le runtime resume` 在 checkpoint 存在时恢复成功至少 1 次。
4. compile fail → revert 路径至少单元测试覆盖 + 1 次 fixture 验证。
5. deploy fail → rollback/escalate 路径至少单元测试覆盖。
6. 旧 control/workflow 文件已删除且全量测试仍 PASS。
7. 文档准确描述新 runtime 架构、手动 break-glass 能力与人工门控。

---

## 最终结果

完成本计划后，loop 将从"WORKFLOW.md 文本 SOP + 离散 CLI 驱动"升级为"自研零依赖状态图 runtime 驱动"，且旧编排架构已从项目中彻底删除。
```

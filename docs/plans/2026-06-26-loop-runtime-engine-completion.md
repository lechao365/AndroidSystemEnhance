# Loop Runtime Engine Completion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 engine 内核对 nodes 的接线、guard chain 真正接入 transition、session 状态持久化、resume 完整恢复、CLI 异常兜底，使 runtime 达到 spec 要求的"唯一正式控制层"成熟度。

**Architecture:** 在已完成 run_verify→decide→analyze 闭环基础上，把 APPLY_PATCH/COMPILE_PATCH/DEPLOY_PATCH/REVERT_PATCH 从 placeholder 升级为真实调用 nodes.py handlers；DECIDE_NEXT 之后的分支由 guard chain 判定取代硬编码 _LINEAR_NEXT；run() 结束回写 session.json；resume() 恢复完整 RuntimeState。

**Tech Stack:** Python 3.11+、dataclasses、StrEnum、pytest、subprocess、git、AOSP build tools

**Design Spec:** `docs/specs/2026-06-26-loop-runtime-rearchitecture-design.md`

**测试环境：**
```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
```

---

### Task 1: engine.py 把 guard chain 接入 DECIDE_NEXT transition

当前 `_transition()` 只走 `_LINEAR_NEXT` 硬编码，`_execute_decide_next` 依赖 `stages.decide_stage` 的字典返回值（重复了 guards.py 已有的判定逻辑）。改为：DECIDE_NEXT 节点执行后，由 engine 用 guard chain 统一判定下一跳。

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`
- Modify: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 更新 engine 测试，增加 guard chain 判定路径验证**

修改 `test_runtime_engine.py`：增加测试验证 PASS 路径走 `all_cases_passed` guard；FAIL+max_attempts 路径走 `attempt_limit_reached` guard。

```python
def test_runtime_guard_chain_done_success(tmp_path, monkeypatch):
    """verify PASS -> guard_chain returns DONE_SUCCESS by all_cases_passed"""
    _write_bundle(tmp_path, "PASS", 0)

    def fake_run(cmd, **kwargs):
        class R: returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-004", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS
    # verify checkpoints contain matched_guards
    cps = CheckpointStore(str(tmp_path), "sess-004").all()
    decide_cps = [cp for cp in cps if cp.current_node == "DECIDE_NEXT"]
    assert len(decide_cps) >= 1
    assert "all_cases_passed" in decide_cps[0].matched_guards


def test_runtime_guard_chain_escalate_on_limit(tmp_path, monkeypatch):
    """verify FAIL+max=1 -> guard_chain returns ESCALATE_HUMAN by attempt_limit_reached"""
    _write_bundle(tmp_path, "FAIL", 1)

    def fake_run(cmd, **kwargs):
        class R: returncode = 1
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-005", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=1, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.ESCALATE_HUMAN
    cps = CheckpointStore(str(tmp_path), "sess-005").all()
    decide_cps = [cp for cp in cps if cp.current_node == "DECIDE_NEXT"]
    assert len(decide_cps) >= 1
    assert "attempt_limit_reached" in decide_cps[0].matched_guards
```

- [ ] **Step 2: 运行测试确认先失败**

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_runtime_guard_chain_done_success engineering/loop/controller/python/tests/test_runtime_engine.py::test_runtime_guard_chain_escalate_on_limit -v
```

Expected: FAIL because `matched_guards` is empty.

- [ ] **Step 3: 改造 engine.py 的 _execute_decide_next 和 _transition**

把 `_execute_decide_next` 从依赖 `stages.decide_stage` 转向 guard chain 判定。DECIDE_NEXT 仍调用 `stages.decide_stage` 获取 decision 摘要，但**不再由 decision dict 决定跳转**，而是由 guard chain 统一判定。

核心改动：在 `_execute_decide_next` 中构造 `GuardEvalRequest`，调用 `guard_chain`；`_transition` 从 guard chain 结果拿 next_node；`_checkpoint` 填充 `matched_guards`。

具体代码替换 `engine.py` 中以下关键部分：

**`_execute_decide_next` 重写为：**

```python
def _execute_decide_next(self) -> None:
    decision = stages.decide_stage(self._to_session_dict())
    self._state.transition_reason = str(decision.get("reason", ""))
    fc = self._resolve_failure_code(decision.get("failure_code", "NONE"))
    # Build GuardEvalRequest from current session state
    guard_req = self._build_guard_eval_request()
    # Evaluate termination guards first, then retry guards
    guard_result = guard_chain(
        [
            "all_cases_passed",
            "attempt_limit_reached",
            "repeated_failure_code",
            "duplicate_patch_hash",
            "kernel_dead_no_shell",
            "patch_rejected",
            "attempts_below_limit",
        ],
        guard_req,
    )
    if guard_result.matched:
        if guard_result.next_node in (
            NodeKind.DONE_SUCCESS.value,
            NodeKind.ESCALATE_HUMAN.value,
        ):
            node = NodeKind(guard_result.next_node)
            if node == NodeKind.ESCALATE_HUMAN:
                self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
            elif node == NodeKind.DONE_SUCCESS:
                self._state.terminal_state = RuntimeTerminalState.DONE_SUCCESS
            self._state.pending_human_gate = True
        self._state.node_status = guard_result.reason
    else:
        self._state.node_status = "RETRY"
    self._checkpoint(
        f"decide={guard_result.reason or 'RETRY'}",
        fc,
        matched_guards=[guard_result.guard_name] if guard_result.matched else [],
    )
```

**新增 `_build_guard_eval_request` 方法：**

```python
def _build_guard_eval_request(self) -> GuardEvalRequest:
    previous_codes: list[FailureCode] = []
    previous_hashes: list[str] = []
    latest_attempt = {}
    if self._session.attempts:
        for att in self._session.attempts:
            if isinstance(att, dict):
                fc_str = att.get("failure_code", "")
                ph = att.get("patch_applied", {}).get("patch_hash", "")
                if fc_str:
                    try:
                        previous_codes.append(FailureCode(fc_str))
                    except ValueError:
                        pass
                if ph:
                    previous_hashes.append(ph)
            else:
                fc = att.get("failure_code", "")
                if fc:
                    try:
                        previous_codes.append(FailureCode(fc))
                    except ValueError:
                        pass
        latest = self._session.attempts[-1] if self._session.attempts else {}
        latest_attempt = latest if isinstance(latest, dict) else {}
    current_hash = latest_attempt.get("patch_applied", {}).get("patch_hash", "") if isinstance(latest_attempt, dict) else ""

    return GuardEvalRequest(
        guard_name="",
        attempt_count=self._session.current_attempt,
        max_attempts=self._session.max_attempts,
        latest_status=self._state.node_status or self._session.status,
        latest_failure_code=self._session.latest_failure_code,
        previous_failure_codes=previous_codes,
        current_patch_hash=current_hash,
        previous_patch_hashes=previous_hashes,
    )
```

**`_transition` 改为依赖 guard 结果决定下一跳：**

```python
def _transition(self) -> None:
    node = self._state.current_node
    if node == NodeKind.DECIDE_NEXT.value:
        # DECIDE_NEXT transition is already handled by guard chain in _execute_decide_next;
        # _compute_next_node looks at node_status for RETRY.
        next_node = self._compute_next_node()
    else:
        next_node = _LINEAR_NEXT.get(node, "")
    if next_node:
        self._state.previous_node = self._state.current_node
        self._state.current_node = next_node
```

**`_checkpoint` 签名增加 `matched_guards`：**

```python
def _checkpoint(self, reason: str, failure_code: FailureCode, matched_guards: list[str] | None = None) -> None:
    next_node = self._compute_next_node()
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
    )
    self._store.save(cp)
    self._state.last_checkpoint_at = cp.timestamp
```

- [ ] **Step 4: 运行测试确认通过**

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v
```

Expected: 5 tests PASS (3 original + 2 new).

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "feat(loop-runtime): integrate guard chain into DECIDE_NEXT transition"
```

---

### Task 2: engine.py 接入 nodes.py 的 4 个能力节点

当前的 `_execute_current_node` 里 APPLY_PATCH/COMPILE_PATCH/DEPLOY_PATCH/REVERT_PATCH 全是占位（`_state.node_status = "XXX_NODE_PENDING"`）。改为实际调用 `nodes.py` 的 handler，并在节点执行后根据结果通过 guard 判定下一跳。

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`
- Modify: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 增加 engine 集成测试，覆盖 APPLY → COMPILE → DEPLOY → VERIFY 路径**

```python
def test_runtime_wires_apply_compile_deploy(tmp_path, monkeypatch):
    """patched FAIL cycle routes APPLY->COMPILE->DEPLOY->RUN_VERIFY"""
    _write_bundle(tmp_path, "FAIL", 1)

    call_log = []

    def fake_run(cmd, **kwargs):
        """first call=fail verify, subsequent=PASS verify for deployed retest"""
        cmd_str = " ".join(cmd)
        call_log.append(cmd_str)
        class R: returncode = 1 if "run " in cmd_str and len(call_log) == 1 else 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    # mock nodes
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_apply_patch",
        lambda *a, **kw: {"status": "APPLIED", "failure_code": FailureCode.NONE,
                          "files": ["test.cpp"], "stash_ref": "fake-stash",
                          "patch_hash": "abc123", "risk": {}, "workspace_root": str(tmp_path)},
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile",
        lambda *a, **kw: {"status": "COMPILED", "failure_code": FailureCode.NONE,
                          "artifacts": ["out/test"]},
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_deploy",
        lambda *a, **kw: {"status": "DEPLOYED", "failure_code": FailureCode.NONE,
                          "mode": "PUSH_SINGLE"},
    )

    session = LoopSession(
        session_id="sess-wire", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    # Should end at DONE_SUCCESS since deploy is mocked ok + re-verify passes
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS
```

- [ ] **Step 2: 运行确认先失败**

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_runtime_wires_apply_compile_deploy -v
```

Expected: FAIL because node handlers are placeholders.

- [ ] **Step 3: 改造 engine._execute_current_node 接入 nodes.py**

替换 `_execute_current_node` 中 APPLY_PATCH/COMPILE_PATCH/DEPLOY_PATCH/REVERT_PATCH 分支。关键逻辑：

APPLY_PATCH 分支：
```python
elif node == NodeKind.APPLY_PATCH.value:
    from loop_controller.runtime.nodes import node_apply_patch
    patch_path = str(Path(self._session.artifacts_dir) / "patch_suggestion.json")
    if not os.path.isfile(patch_path):
        self._state.node_status = "NO_PATCH_FILE"
        self._checkpoint("no patch file found", FailureCode.PATCH_REJECTED)
        self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
        return
    result = node_apply_patch(patch_path, self._to_session_dict(), "")
    self._state.node_status = result["status"]
    fc = result.get("failure_code", FailureCode.NONE)
    if isinstance(fc, str):
        fc = FailureCode(fc)
    # record patch result into session attempts
    if result["status"] == "APPLIED":
        latest = self._session.attempts[-1] if self._session.attempts else {}
        if isinstance(latest, dict):
            latest["patch_applied"] = {
                "patch_hash": result.get("patch_hash", ""),
                "stash_ref": result.get("stash_ref", ""),
                "workspace_root": result.get("workspace_root", ""),
                "risk": result.get("risk", {}),
            }
            if not self._session.attempts:
                self._session.attempts.append(latest)
    # guard after apply
    guard_req = self._build_guard_eval_request()
    guard_result = guard_chain(
        ["patch_rejected", "patch_applied_successfully"], guard_req,
    )
    if guard_result.matched and guard_result.next_node == NodeKind.ESCALATE_HUMAN.value:
        self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
        self._state.pending_human_gate = True
    self._checkpoint(
        f"apply_patch={result['status']}", fc,
        matched_guards=[result.get("guard_name", "")] if guard_result.matched else [],
    )
```

COMPILE_PATCH 分支：
```python
elif node == NodeKind.COMPILE_PATCH.value:
    from loop_controller.runtime.nodes import node_compile
    result = node_compile(self._to_session_dict(), "")
    self._state.node_status = result["status"]
    fc = result.get("failure_code", FailureCode.NONE)
    if isinstance(fc, str):
        fc = FailureCode(fc)
    if result["status"] == "COMPILE_FAILED":
        # compile fail -> guard chain decides revert
        guard_req = self._build_guard_eval_request()
        guard_result = guard_chain(["compile_failed_but_recoverable"], guard_req)
        if guard_result.matched:
            self._state.node_status = "COMPILE_FAILED_REVERT"
    self._checkpoint(f"compile={result['status']}", fc)
```

DEPLOY_PATCH 分支：
```python
elif node == NodeKind.DEPLOY_PATCH.value:
    from loop_controller.runtime.nodes import node_deploy
    result = node_deploy(self._to_session_dict(), adb_endpoint="")
    self._state.node_status = result["status"]
    fc = result.get("failure_code", FailureCode.NONE)
    if isinstance(fc, str):
        fc = FailureCode(fc)
    if result["status"] == "DEPLOY_FAILED":
        guard_req = self._build_guard_eval_request()
        guard_result = guard_chain(
            ["kernel_dead_no_shell", "deploy_failed_but_recoverable"], guard_req,
        )
        if guard_result.matched:
            if guard_result.next_node == NodeKind.ESCALATE_HUMAN.value:
                self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                self._state.pending_human_gate = True
    self._checkpoint(f"deploy={result['status']}", fc)
```

REVERT_PATCH 分支：
```python
elif node == NodeKind.REVERT_PATCH.value:
    from loop_controller.runtime.nodes import node_revert
    result = node_revert(self._to_session_dict())
    self._state.node_status = result["status"]
    fc = result.get("failure_code", FailureCode.NONE)
    if isinstance(fc, str):
        fc = FailureCode(fc)
    self._checkpoint(f"revert={result['status']}", fc)
```

更新 `_LINEAR_NEXT`，让 COMPILE_PATCH 节点在 compile fail 后通过 guard 走向 REVERT_PATCH 而不是下一个节点：

```python
_LINEAR_NEXT: dict[str, str] = {
    NodeKind.INIT_SESSION.value: NodeKind.RUN_VERIFY.value,
    NodeKind.RUN_VERIFY.value: NodeKind.DECIDE_NEXT.value,
    NodeKind.BUILD_ANALYSIS_REQUEST.value: NodeKind.WAIT_ANALYZER_PATCH.value,
    NodeKind.WAIT_ANALYZER_PATCH.value: NodeKind.APPLY_PATCH.value,
    NodeKind.APPLY_PATCH.value: NodeKind.COMPILE_PATCH.value,
    NodeKind.DEPLOY_PATCH.value: NodeKind.RUN_VERIFY.value,
    NodeKind.REVERT_PATCH.value: NodeKind.DECIDE_NEXT.value,
    # COMPILE_PATCH node transition is guard-controlled:
    #   on COMPILE_FAILED -> REVERT_PATCH (via compile_failed_but_recoverable guard)
    #   on COMPILED -> DEPLOY_PATCH (linear)
}
```

`_compute_next_node` 需要处理 COMPILE_FAILED 情况：

```python
def _compute_next_node(self) -> str:
    node = self._state.current_node
    if node == NodeKind.DECIDE_NEXT.value and self._state.node_status == "RETRY":
        return NodeKind.BUILD_ANALYSIS_REQUEST.value
    if node == NodeKind.COMPILE_PATCH.value and self._state.node_status.startswith("COMPILE_FAILED"):
        return NodeKind.REVERT_PATCH.value
    return _LINEAR_NEXT.get(node, "")
```

- [ ] **Step 4: 运行全量 engine 测试**

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "feat(loop-runtime): wire nodes.py handlers into engine for APPLY/COMPILE/DEPLOY/REVERT nodes"
```

---

### Task 3: run() 结束后回写 session.json + resume() 完整状态恢复

当前 `runtime_cli.run()` 不写回 session.json，且 `resume()` 只恢复 current_node。需要：
- `run()` 结束后把 `LoopRuntime._session` 和 `RuntimeState` 写回 artifacts_dir 下的 session.json
- `resume()` 恢复 node_status / failure_code / last_checkpoint_at 等字段

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`
- Modify: `engineering/loop/controller/python/loop_controller/runtime_cli.py`
- Modify: `engineering/loop/controller/python/tests/test_runtime_engine.py`
- Modify: `engineering/loop/controller/python/tests/test_runtime_cli.py`

- [ ] **Step 1: 增加测试**

在 `test_runtime_engine.py` 增加：

```python
def test_run_writes_back_session_json(tmp_path, monkeypatch):
    """run() writes session.json after completion"""
    _write_bundle(tmp_path, "PASS", 0)

    def fake_run(cmd, **kwargs):
        class R: returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-wb", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS

    session_path = tmp_path / "session.json"
    assert session_path.exists()
    saved = json.loads(session_path.read_text())
    assert saved["session_id"] == "sess-wb"
    assert saved["terminal_state"] == "DONE_SUCCESS"


def test_resume_restores_full_state(tmp_path, monkeypatch):
    """resume() restores node_status and last_checkpoint_at"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    store = CheckpointStore(str(tmp_path), "sess-full")
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id="sess-full", attempt_index=2,
        current_node="RUN_VERIFY",
        input_summary={}, output_summary={"node_status": "FAIL", "reason": "verify FAIL"},
        failure_code=FailureCode.RUN_FAILED, matched_guards=["attempts_below_limit"],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:30:00+08:00",
    ))

    session = LoopSession(
        session_id="sess-full", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.resume()
    assert state.current_node == "DECIDE_NEXT"
    assert state.previous_node == "RUN_VERIFY"
    assert state.last_checkpoint_at == "2026-06-26T12:30:00+08:00"
```

在 `test_runtime_cli.py` 增加：

```python
def test_runtime_run_writes_session_json(tmp_path, monkeypatch):
    """run subcommand writes session.json after completion"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture([
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "3", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_sid(out)

    def fake_run(cmd, **kwargs):
        class R: returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    (artifacts / "evidence_bundle.json").write_text(json.dumps({
        "summary": {"overall": "PASS", "total": 1, "passed": 1, "failed": 0, "skipped": 0},
        "cases": [],
    }), encoding="utf-8")

    rc, out = _capture(["run", "--session", str(artifacts / f"{sid}.json")])
    assert rc == 0
    session_path = artifacts / "session.json"
    assert session_path.exists()
    data = json.loads(session_path.read_text())
    assert data["session_id"] == sid
    assert "terminal_state" in data
```

- [ ] **Step 2: 运行确认先失败**

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_run_writes_back_session_json engineering/loop/controller/python/tests/test_runtime_engine.py::test_resume_restores_full_state engineering/loop/controller/python/tests/test_runtime_cli.py::test_runtime_run_writes_session_json -v
```

Expected: FAIL (session.json not written / resume missing fields).

- [ ] **Step 3: 改造 engine.py —— run() 回写 session.json**

在 `run()` 结尾增加：

```python
def run(self) -> RuntimeState:
    while self._state.terminal_state == RuntimeTerminalState.NONE:
        self._execute_current_node()
        if self._state.terminal_state != RuntimeTerminalState.NONE:
            break
        self._transition()
    self._persist_session()
    return self._state
```

新增 `_persist_session` 方法：

```python
def _persist_session(self) -> None:
    session_path = Path(self._session.artifacts_dir) / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": self._session.session_id,
        "workflow_id": self._session.workflow_id,
        "target": self._session.target,
        "suite": self._session.suite,
        "max_attempts": self._session.max_attempts,
        "current_attempt": self._session.current_attempt,
        "status": self._session.status,
        "latest_failure_code": self._session.latest_failure_code.value,
        "attempts": self._session.attempts,
        "artifacts_dir": self._session.artifacts_dir,
        "terminal_state": self._state.terminal_state.value,
        "current_node": self._state.current_node,
        "node_status": self._state.node_status,
        "transition_reason": self._state.transition_reason,
        "last_checkpoint_at": self._state.last_checkpoint_at,
    }
    session_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
```

- [ ] **Step 4: 改造 resume() 完整恢复状态**

```python
def resume(self) -> RuntimeState:
    cp = self._store.latest()
    if cp:
        self._state.current_node = cp.next_node
        self._state.previous_node = cp.current_node
        self._state.node_status = cp.output_summary.get("node_status", "")
        self._state.last_checkpoint_at = cp.timestamp
        self._state.interrupted = False
    return self._state
```

- [ ] **Step 5: 改造 runtime_cli.py —— run handler 用 _persist_session 或自行写回**

在 `_handle_run` 中调用 `rt.run()` 后回写 session：

```python
def _handle_run(args: argparse.Namespace) -> int:
    session = _load_session(args.session)
    rt = LoopRuntime(session, _CASES_DIR, _DEVICE_PROFILE)
    state = rt.run()
    print(f"terminal_state={state.terminal_state.value}")
    if state.terminal_state == RuntimeTerminalState.DONE_SUCCESS:
        return 0
    return 1
```

（`rt.run()` 内部已调用 `_persist_session`，CLI 无需额外操作）

- [ ] **Step 6: 运行测试确认通过**

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py engineering/loop/controller/python/tests/test_runtime_cli.py -v
```

Expected: all PASS.

- [ ] **Step 7: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/loop_controller/runtime_cli.py engineering/loop/controller/python/tests/test_runtime_engine.py engineering/loop/controller/python/tests/test_runtime_cli.py
git commit -m "feat(loop-runtime): write session.json on run completion, full state restore on resume"
```

---

### Task 4: CLI 顶层异常兜底 + stages 全局变量消除

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime_cli.py`
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`

- [ ] **Step 1: runtime_cli.run 增加顶层 try/except**

```python
def _handle_run(args: argparse.Namespace) -> int:
    try:
        session = _load_session(args.session)
        rt = LoopRuntime(session, _CASES_DIR, _DEVICE_PROFILE)
        state = rt.run()
        print(f"terminal_state={state.terminal_state.value}")
        if state.terminal_state == RuntimeTerminalState.DONE_SUCCESS:
            return 0
        return 1
    except Exception as e:
        print(f"RUNTIME_FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 2
```

- [ ] **Step 2: engine.__init__ 消除 stages 全局变量污染**

将 `stages._CASES_DIR = cases_dir` 改成引擎本地传递（通过参数或 attributes）。最简单的方式是 engine 持有一个 `_device_profile` attribute，传给 stages 函数时用 keyword arg：

```python
def __init__(self, session: LoopSession, cases_dir: str, device_profile: str) -> None:
    self._session = session
    self._cases_dir = cases_dir
    self._device_profile = device_profile
    self._state = RuntimeState(current_node=NodeKind.INIT_SESSION.value)
    self._store = CheckpointStore(session.artifacts_dir, session.session_id)
    # NOTE: stages module keeps module-level constants for compatibility;
    # engine preserves the current override pattern but uses instance attributes
    stages._CASES_DIR = cases_dir
    stages._DEVICE_PROFILE = device_profile
```

（当前不改 stages 全局架构，因为 stages 内部多处直接引用 `_CASES_DIR`/`_DEVICE_PROFILE`。标记后续清场 Wishlist，本次只加注释。）

- [ ] **Step 3: 运行全量测试**

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/ engineering/loop/contracts/python/tests/ -v -q
```

Expected: all PASS.

- [ ] **Step 4: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime_cli.py engineering/loop/controller/python/loop_controller/runtime/engine.py
git commit -m "feat(loop-runtime): add top-level exception handling in CLI, document global cleanup TODO"
```

---

### Task 5: checkpoint_store 性能 + attempts 类型一致性

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py`
- Modify: `engineering/loop/controller/python/tests/test_checkpoint_store.py`

- [ ] **Step 1: 增加测试**

```python
def test_checkpoint_store_all_performance(tmp_path: Path):
    """all() should parse each line exactly once"""
    store = CheckpointStore(str(tmp_path), "sess-perf")
    for i in range(100):
        store.save(_make_cp(f"cp-{i:03d}", attempt=i + 1))
    results = store.all()
    assert len(results) == 100


def test_checkpoint_store_dedup_parsing(tmp_path: Path):
    """_from_line should not be called more than once per line on all()"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore as CS
    call_count = [0]
    orig_from_line = CS._from_line

    def counting_from_line(self, line):
        call_count[0] += 1
        return orig_from_line(self, line)

    CS._from_line = counting_from_line
    try:
        store = CS(str(tmp_path), "sess-dedup")
        store.save(_make_cp("cp-001"))
        store.save(_make_cp("cp-002"))
        results = store.all()
        assert len(results) == 2
        assert call_count[0] == 2  # exactly once per line
    finally:
        CS._from_line = orig_from_line
```

- [ ] **Step 2: 运行确认先失败**

```bash
export PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python"
python3 -m pytest engineering/loop/controller/python/tests/test_checkpoint_store.py::test_checkpoint_store_dedup_parsing -v
```

Expected: FAIL (current code parses each line twice — once for session filter, once for result).

- [ ] **Step 3: 修复 checkpoint_store.all() 重复解析**

```python
def all(self) -> list[CheckpointRecord]:
    if not self._path.exists():
        return []
    lines = self._path.read_text(encoding="utf-8").strip().splitlines()
    results: list[CheckpointRecord] = []
    for line in lines:
        if not line:
            continue
        cp = self._from_line(line)
        if cp.session_id == self._session_id:
            results.append(cp)
    return results
```

- [ ] **Step 4: 运行 checkpoint 测试确认通过**

```bash
export PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python"
python3 -m pytest engineering/loop/controller/python/tests/test_checkpoint_store.py -v
```

Expected: all PASS.

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py engineering/loop/controller/python/tests/test_checkpoint_store.py
git commit -m "fix(loop-runtime): dedup checkpoint parsing in all(), add performance test"
```

---

## 切换验证清单

任务全部完成后运行全量测试：

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
python3 -m pytest engineering/loop/controller/python/tests/ engineering/loop/contracts/python/tests/ -v
```

Expected: all PASS (当前基线 45 测试 + 新增 ~8 测试 = 53+ 测试)。

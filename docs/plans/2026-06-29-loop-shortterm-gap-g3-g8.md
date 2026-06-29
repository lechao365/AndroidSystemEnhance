# G3 + G8 短期 Gap 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 loop 框架补齐 G8 文档一致性元测试和 G3 analyzer 轨迹上下文，提升自主修复有效性与文档守护能力。

**Architecture:** G8 是纯测试新增（8 个元测试 + 1 处 README 补全），零生产代码改动。G3 是 `AnalysisRequest` 新增 `prior_attempts` 字段 + `stages.py` 注入轨迹 + `OpencodeAnalyzer._build_prompt` 消费轨迹，向后兼容。

**Tech Stack:** Python 3.11+, pytest, dataclasses

**关联设计:** `docs/specs/2026-06-29-loop-shortterm-gap-g3-g8-design.md`

---

## 测试环境

所有 pytest 命令需设置 PYTHONPATH：

```bash
export PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
```

或用快捷变量（下文用 `$P` 代指上述长串）：

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
```

全量回归命令：
```bash
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/connection/providers/rp5-serial/python/tests/ engineering/loop/connection/providers/adb/python/tests/ engineering/loop/deploy/python/tests/ engineering/loop/contracts/python/tests/ -q
```

---

## Task 1: G8 前置 — 补全 README 状态机图 DONE_FAILURE

**Files:**
- Modify: `engineering/loop/controller/README.md:77-84`

- [ ] **Step 1: 查看当前状态机图**

当前 `controller/README.md:77-84` 状态机图缺 `DONE_FAILURE`，仅 11 节点（实现有 12 个 NodeKind）：

```text
INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT
  ├─ DONE_SUCCESS                          (全 PASS)
  ├─ ESCALATE_HUMAN                        (FAIL>=max / 重复失败 / 重复补丁 / kernel dead / ...)
  └─ BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH
                                -> APPLY_PATCH -> COMPILE_PATCH -> DEPLOY_PATCH -> RUN_VERIFY (回环重验)
                                -> REVERT_PATCH -> DECIDE_NEXT                              (编译/部署失败回滚后重判)
```

- [ ] **Step 2: 补全 DONE_FAILURE 节点**

将状态机图改为：

```text
INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT
  ├─ DONE_SUCCESS                          (全 PASS)
  ├─ ESCALATE_HUMAN                        (FAIL>=max / 重复失败 / 重复补丁 / kernel dead / ...)
  ├─ DONE_FAILURE                          (系统异常终止)
  └─ BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH
                                -> APPLY_PATCH -> COMPILE_PATCH -> DEPLOY_PATCH -> RUN_VERIFY (回环重验)
                                -> REVERT_PATCH -> DECIDE_NEXT                              (编译/部署失败回滚后重判)
```

- [ ] **Step 3: 提交**

```bash
git add engineering/loop/controller/README.md
git commit -m "文档(controller): 状态机图补全 DONE_FAILURE 节点对齐 NodeKind 实现"
```

---

## Task 2: G8 — 创建文档一致性元测试

**Files:**
- Create: `engineering/loop/controller/python/tests/test_docs_consistency.py`

- [ ] **Step 1: 创建测试文件，写全部 8 个测试**

```python
"""文档一致性元测试：守护 contracts/controller README 与实现层真相不漂移。

借鉴 test_diagnosis_contract_docs.py 的 _repo_root / _read helper 模式。
新增/删除 FailureCode、guard、NodeKind、contracts 导出符号时，必须同步改 README。
"""
from pathlib import Path

from loop_contracts import __all__ as _contracts_all
from loop_contracts.failure_codes import FailureCode
from loop_controller.runtime.guards import _GUARD_REGISTRY
from loop_controller.runtime.types import NodeKind


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    while path.name != "engineering":
        if path == path.parent:
            raise RuntimeError("engineering/ root not found")
        path = path.parent
    return path.parent


def _read(relative_path: str) -> str:
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


# ---------- contracts/README.md 守护 ----------

def test_failure_code_count_matches_readme() -> None:
    """守护点 1: FailureCode 成员数 = 17，README 必须含 '17 项'。"""
    count = len(list(FailureCode))
    assert count == 17, f"FailureCode 成员数变了: {count}，请同步改此测试和 README"
    text = _read("engineering/loop/contracts/README.md")
    assert "17 项" in text, "contracts/README.md 缺少 '17 项'，请同步更新"


def test_failure_code_names_in_readme() -> None:
    """守护点 2: 每个 FailureCode 成员名都出现在 README 中。"""
    text = _read("engineering/loop/contracts/README.md")
    missing = [name for name in FailureCode.__members__ if name not in text]
    assert not missing, f"contracts/README.md 缺少这些 FailureCode 名: {missing}"


def test_contracts_all_count_matches_readme() -> None:
    """守护点 3: contracts __all__ 长度 = 9，README 必须含 '九符号'。"""
    count = len(_contracts_all)
    assert count == 9, f"contracts __all__ 长度变了: {count}，请同步改此测试和 README"
    text = _read("engineering/loop/contracts/README.md")
    assert "九符号" in text or "9" in text, "contracts/README.md 缺少导出符号数量说明"


def test_contracts_all_names_in_readme() -> None:
    """守护点 4: 每个 contracts 导出符号名都出现在 README 中。"""
    text = _read("engineering/loop/contracts/README.md")
    missing = [name for name in _contracts_all if name not in text]
    assert not missing, f"contracts/README.md 缺少这些导出符号名: {missing}"


def test_contracts_dataclass_count_matches_readme() -> None:
    """守护点 5: dataclass 数 = 6，README 必须含 '六 dataclass'。"""
    text = _read("engineering/loop/contracts/README.md")
    assert "六 dataclass" in text or "6" in text, "contracts/README.md 缺少 dataclass 数量说明"


# ---------- controller/README.md 守护 ----------

def test_guards_count_matches_readme() -> None:
    """守护点 6: guard 数量 = 16，README 必须含 '16 个'。"""
    count = len(_GUARD_REGISTRY)
    assert count == 16, f"guard 数量变了: {count}，请同步改此测试和 README"
    text = _read("engineering/loop/controller/README.md")
    assert "16 个" in text, "controller/README.md 缺少 '16 个'，请同步更新"


def test_guards_names_in_readme() -> None:
    """守护点 7: 每个 guard 名都出现在 controller README 中。"""
    text = _read("engineering/loop/controller/README.md")
    missing = [name for name in _GUARD_REGISTRY if name not in text]
    assert not missing, f"controller/README.md 缺少这些 guard 名: {missing}"


def test_nodekind_names_in_readme() -> None:
    """守护点 8: 每个 NodeKind 成员名都出现在 controller README 中。"""
    text = _read("engineering/loop/controller/README.md")
    missing = [name for name in NodeKind.__members__ if name not in text]
    assert not missing, f"controller/README.md 缺少这些 NodeKind 名: {missing}"
```

- [ ] **Step 2: 运行测试验证通过**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_docs_consistency.py -v
```

Expected: 8 passed

- [ ] **Step 3: 反证测试有效性 — 删除 README 中一个 guard 名验证失败**

```bash
# 临时删除一个 guard 名，跑测试看是否失败
PYTHONPATH="$P" python -c "
text = open('engineering/loop/controller/README.md').read()
# 模拟删除 boot_timeout_kernel_panic
text2 = text.replace('boot_timeout_kernel_panic', 'REMOVED')
open('engineering/loop/controller/README.md','w').write(text2)
"
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_docs_consistency.py::test_guards_names_in_readme -v
# Expected: FAIL
# 然后还原:
git checkout engineering/loop/controller/README.md
```

- [ ] **Step 4: 提交**

```bash
git add engineering/loop/controller/python/tests/test_docs_consistency.py
git commit -m "测试(controller): 新增 G8 文档一致性元测试，守护 8 个对照点"
```

---

## Task 3: G8 — 全量回归

- [ ] **Step 1: 跑全量回归确保零回归**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/connection/providers/rp5-serial/python/tests/ engineering/loop/connection/providers/adb/python/tests/ engineering/loop/deploy/python/tests/ engineering/loop/contracts/python/tests/ -q
```

Expected: 全部 passed（基线 602 + G8 新增 8 = 610）

---

## Task 4: G3 — AnalysisRequest 新增 prior_attempts 字段（TDD）

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py:18-28`
- Test: `engineering/loop/controller/python/tests/test_analyzer_protocol.py`

- [ ] **Step 1: 先写失败测试 — 字段默认值和兼容性**

在 `test_analyzer_protocol.py` 末尾追加：

```python
def test_analysis_request_prior_attempts_default_empty():
    """G3: AnalysisRequest 新增 prior_attempts 字段，默认空列表。"""
    req = AnalysisRequest(session_id="s", attempt_index=0)
    assert req.prior_attempts == []


def test_analysis_request_prior_attempts_accepts_list():
    """G3: prior_attempts 接受列表值。"""
    req = AnalysisRequest(
        session_id="s", attempt_index=0,
        prior_attempts=[{"attempt_index": 0, "patch_hash": "abc123"}],
    )
    assert len(req.prior_attempts) == 1
    assert req.prior_attempts[0]["patch_hash"] == "abc123"
```

- [ ] **Step 2: 运行测试验证失败（字段不存在）**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_analyzer_protocol.py::test_analysis_request_prior_attempts_default_empty engineering/loop/controller/python/tests/test_analyzer_protocol.py::test_analysis_request_prior_attempts_accepts_list -v
```

Expected: FAIL with `AttributeError` or dataclass unexpected keyword

- [ ] **Step 3: 加字段实现**

在 `analyzer_protocol.py:18-28` 的 `AnalysisRequest` dataclass 中，在 `suite` 字段后新增一行：

```python
@dataclass
class AnalysisRequest:
    session_id: str
    attempt_index: int
    failed_cases: list[dict] = field(default_factory=list)
    evidence_bundle_path: str = ""
    collectors_output: dict = field(default_factory=dict)
    workspace_diff_so_far: str = ""
    hints: str = ""
    target: str = ""
    suite: str = ""
    prior_attempts: list[dict] = field(default_factory=list)
```

- [ ] **Step 4: 运行测试验证通过**

```bash
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_analyzer_protocol.py::test_analysis_request_prior_attempts_default_empty engineering/loop/controller/python/tests/test_analyzer_protocol.py::test_analysis_request_prior_attempts_accepts_list -v
```

Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py engineering/loop/controller/python/tests/test_analyzer_protocol.py
git commit -m "功能(analyzer): G3 AnalysisRequest 新增 prior_attempts 字段（向后兼容）"
```

---

## Task 5: G3 — 反序列化兼容性测试（TDD）

**Files:**
- Test: `engineering/loop/controller/python/tests/test_analyzer_protocol.py`

- [ ] **Step 1: 写测试 — 旧 JSON（无 prior_attempts）可反序列化**

在 `test_analyzer_protocol.py` 末尾追加：

```python
def test_analysis_request_deserialize_old_json_without_prior_attempts():
    """G3: 旧 checkpoint JSON（无 prior_attempts 键）能正常反序列化。"""
    import json
    from loop_controller.analyzer_protocol import AnalysisRequest

    old_json = json.dumps({
        "session_id": "s",
        "attempt_index": 1,
        "failed_cases": [{"id": "TC-01"}],
        "evidence_bundle_path": "/tmp/eb.json",
        "collectors_output": {},
        "workspace_diff_so_far": "",
        "hints": "",
        "target": "lciod",
        "suite": "hal",
    })
    data = json.loads(old_json)
    request = AnalysisRequest(**{
        k: v for k, v in data.items()
        if k in AnalysisRequest.__dataclass_fields__
    })
    assert request.prior_attempts == []
    assert request.target == "lciod"
```

- [ ] **Step 2: 运行验证通过（字段已有默认值，应直接通过）**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_analyzer_protocol.py::test_analysis_request_deserialize_old_json_without_prior_attempts -v
```

Expected: passed

- [ ] **Step 3: 提交**

```bash
git add engineering/loop/controller/python/tests/test_analyzer_protocol.py
git commit -m "测试(analyzer): G3 验证旧 JSON 反序列化兼容 prior_attempts"
```

---

## Task 6: G3 — stages.py 注入 prior_attempts（TDD）

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/stages.py:235-269`
- Test: `engineering/loop/controller/python/tests/test_stages.py`

- [ ] **Step 1: 先写失败测试 — 轨迹投影逻辑**

在 `test_stages.py` 末尾追加（如果该文件不存在则创建，头部加 `import os` + `from loop_controller.stages import analyze_request_stage`）：

```python
import json
import os
from loop_controller.stages import analyze_request_stage


def test_analyze_request_stage_injects_prior_attempts(tmp_path, monkeypatch):
    """G3: analyze_request_stage 从 attempts 历史投影 prior_attempts。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    # 构造两轮历史：第 0 轮有补丁失败，第 1 轮是当前轮
    session_data = {
        "session_id": "s1",
        "current_attempt": 1,
        "artifacts_dir": str(artifacts),
        "attempts": [
            {
                "attempt_index": 0,
                "failed_cases": [{"id": "TC-01"}],
                "failed_count": 1,
                "failure_code": "COMPILE_FAILED",
                "patch_applied": {
                    "patch_hash": "abc123",
                    "files": ["vendor/lechao/foo.c"],
                },
                "compile_result": {"error": "implicit declaration of function 'bar'"},
            },
            {
                "attempt_index": 1,
                "failed_cases": [{"id": "TC-02"}],
                "failed_count": 1,
                "failure_code": "RUN_FAILED",
                "evidence_path": "",
            },
        ],
        "target": "lciod",
        "suite": "hal",
    }
    monkeypatch.setattr("loop_controller.stages._get_workspace_diff", lambda: "")
    req_path = analyze_request_stage(session_data)
    data = json.loads(open(req_path).read())
    assert len(data["prior_attempts"]) == 1
    pa = data["prior_attempts"][0]
    assert pa["patch_hash"] == "abc123"
    assert pa["failure_code"] == "COMPILE_FAILED"
    assert pa["patch_files"] == ["vendor/lechao/foo.c"]
    assert "bar" in pa["failure_summary"]


def test_analyze_request_stage_skips_attempts_without_patch(tmp_path, monkeypatch):
    """G3: 无 patch_applied 的 attempt（首轮纯 verify）不进 prior_attempts。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session_data = {
        "session_id": "s1",
        "current_attempt": 2,
        "artifacts_dir": str(artifacts),
        "attempts": [
            {
                "attempt_index": 0,
                "failed_cases": [{"id": "TC-01"}],
                "failed_count": 1,
                "failure_code": "RUN_FAILED",
                # 无 patch_applied
            },
            {
                "attempt_index": 1,
                "failed_cases": [{"id": "TC-01"}],
                "failed_count": 1,
                "failure_code": "COMPILE_FAILED",
                "patch_applied": {
                    "patch_hash": "def456",
                    "files": ["foo.c"],
                },
            },
            {
                "attempt_index": 2,
                "failed_cases": [{"id": "TC-02"}],
                "failed_count": 1,
                "evidence_path": "",
            },
        ],
        "target": "lciod",
        "suite": "hal",
    }
    monkeypatch.setattr("loop_controller.stages._get_workspace_diff", lambda: "")
    req_path = analyze_request_stage(session_data)
    data = json.loads(open(req_path).read())
    # 只有 1 条进轨迹（第 1 轮有 patch_applied，第 0 轮无）
    assert len(data["prior_attempts"]) == 1
    assert data["prior_attempts"][0]["patch_hash"] == "def456"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_stages.py::test_analyze_request_stage_injects_prior_attempts engineering/loop/controller/python/tests/test_stages.py::test_analyze_request_stage_skips_attempts_without_patch -v
```

Expected: FAIL（`prior_attempts` 不在 JSON 输出中，或 `_build_prior_attempts` 不存在）

- [ ] **Step 3: 在 stages.py 中实现 `_build_prior_attempts` 和 `_summarize_failure`**

在 `stages.py` 的 `analyze_request_stage` 函数**之前**（约第 233 行，即函数定义前）新增两个辅助函数：

```python
def _summarize_failure(attempt: dict) -> str:
    """从 attempt 生成一行失败摘要（优先 compile_error 首行，其次 failed_case id，最后 failure_code）。"""
    compile_error = (attempt.get("compile_result") or {}).get("error", "")
    if compile_error:
        return compile_error.splitlines()[0][:200]
    failed_cases = attempt.get("failed_cases") or []
    if failed_cases:
        ids = [c.get("id", "?") for c in failed_cases[:5]]
        return f"failed: {', '.join(ids)}"
    fc = attempt.get("failure_code", "")
    return fc or "unknown"


def _build_prior_attempts(attempts: list[dict]) -> list[dict]:
    """从 session attempts 投影精简轨迹（排除最后一轮=当前轮，跳过无补丁的纯 verify 轮）。"""
    prior = []
    for i, a in enumerate(attempts[:-1]):
        patch_applied = a.get("patch_applied") or {}
        if not patch_applied:
            continue
        prior.append({
            "attempt_index": a.get("attempt_index", i),
            "patch_hash": patch_applied.get("patch_hash", ""),
            "failure_code": a.get("failure_code", ""),
            "failed_count": a.get("failed_count", 0),
            "patch_files": patch_applied.get("files", []),
            "failure_summary": _summarize_failure(a),
        })
    return prior
```

- [ ] **Step 4: 在 `analyze_request_stage` 的 AnalysisRequest 构造中注入 prior_attempts**

将 `stages.py:254-263` 的 `request = AnalysisRequest(...)` 改为（新增最后一行 `prior_attempts=...`）：

```python
    request = AnalysisRequest(
        session_id=session_data.get("session_id", ""),
        attempt_index=session_data.get("current_attempt", 0),
        failed_cases=failed_cases,
        evidence_bundle_path=evidence_path,
        collectors_output=collectors_output,
        workspace_diff_so_far=_get_workspace_diff(),
        target=session_data.get("target", ""),
        suite=session_data.get("suite", ""),
        prior_attempts=_build_prior_attempts(session_data.get("attempts", [])),
    )
```

- [ ] **Step 5: 运行测试验证通过**

```bash
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_stages.py::test_analyze_request_stage_injects_prior_attempts engineering/loop/controller/python/tests/test_stages.py::test_analyze_request_stage_skips_attempts_without_patch -v
```

Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/stages.py engineering/loop/controller/python/tests/test_stages.py
git commit -m "功能(stages): G3 analyze_request_stage 注入 prior_attempts 精简轨迹"
```

---

## Task 7: G3 — OpencodeAnalyzer prompt 注入历史（TDD）

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py:499-529`
- Test: `engineering/loop/controller/python/tests/test_opencode_analyzer.py`

- [ ] **Step 1: 先写失败测试 — prompt 含历史段落**

在 `test_opencode_analyzer.py` 末尾追加：

```python
def test_opencode_prompt_includes_history_when_prior_attempts_exist(tmp_path):
    """G3: prior_attempts 非空时 prompt 含'历史尝试'段落。"""
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    req = AnalysisRequest(
        session_id="s", attempt_index=2, target="lciod",
        failed_cases=[{"id": "TC-02", "failure_reason": "still failing"}],
        prior_attempts=[
            {
                "attempt_index": 0,
                "patch_hash": "abc123",
                "failure_code": "COMPILE_FAILED",
                "failed_count": 2,
                "patch_files": ["vendor/lechao/foo.c", "vendor/lechao/bar.h"],
                "failure_summary": "implicit declaration of function 'bar'",
            },
        ],
    )
    prompt = analyzer._build_prompt(req)
    assert "历史尝试" in prompt
    assert "abc123" not in prompt  # hash 本身不应直接渲染（太长无意义）
    assert "foo.c" in prompt
    assert "COMPILE_FAILED" in prompt
    assert "implicit declaration" in prompt


def test_opencode_prompt_no_history_section_when_empty(tmp_path):
    """G3: prior_attempts 为空时不渲染历史段落。"""
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    req = AnalysisRequest(session_id="s", attempt_index=1, target="lciod")
    prompt = analyzer._build_prompt(req)
    assert "历史尝试" not in prompt
```

- [ ] **Step 2: 运行测试验证失败**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_opencode_analyzer.py::test_opencode_prompt_includes_history_when_prior_attempts_exist engineering/loop/controller/python/tests/test_opencode_analyzer.py::test_opencode_prompt_no_history_section_when_empty -v
```

Expected: FAIL（prompt 中无"历史尝试"段落）

- [ ] **Step 3: 修改 `_build_prompt` 注入历史段落**

将 `analyzer_protocol.py:499-529` 的 `_build_prompt` 方法改为（在 `## 失败用例` 段落之前插入历史段落）：

```python
    def _build_prompt(self, request: AnalysisRequest) -> str:
        lines = [
            "你是代码修复助手。以下测试用例失败了，请分析根因并生成修复补丁。",
            "",
            f"Target: {request.target}",
            f"Suite: {request.suite}",
        ]
        # G3: 注入历史尝试轨迹（prior_attempts 非空时渲染）
        if request.prior_attempts:
            lines.extend(["", "## 历史尝试（请避免重复方向）"])
            for pa in request.prior_attempts:
                idx = pa.get("attempt_index", "?")
                files = ", ".join(pa.get("patch_files", [])) or "(未知)"
                fc = pa.get("failure_code", "unknown")
                summary = pa.get("failure_summary", "")
                lines.extend([
                    "",
                    f"### 尝试 #{idx}",
                    f"- 补丁文件: {files}",
                    f"- 失败码: {fc}",
                    f"- 失败摘要: {summary}",
                ])
        lines.extend(["", "## 失败用例"])
        for fc in request.failed_cases:
            lines.append(f"- {fc.get('id', '?')}: {fc.get('failure_reason', '?')}")
            lines.append(f"  command: {fc.get('command', '?')}")
        if request.evidence_bundle_path:
            lines.append(f"\n## EvidenceBundle\n路径: {request.evidence_bundle_path}")
            lines.append("（可读取该文件获取完整上下文）")
        lines.extend([
            "",
            "## 重要约束",
            f"1. 只修复源码 bug，禁止修改测试用例定义（.yaml/.json case 文件）。",
            f"2. workspace_path 必须是相对 workspace 根目录的源码路径（如 vendor/lechao/.../foo.cpp）。",
            "3. old_marker 必须是源码中存在的唯一文本（精确匹配，含缩进）。",
            "4. 优先排查：注入的错误日志、逻辑反转、条件错误等。",
            "5. 可使用 grep/rg 在 workspace 中搜索相关代码定位根因。",
            "6. 避免重复历史尝试中已失败的修复方向。",
            "",
            "## 输出要求",
            "输出严格 JSON 数组，每个元素格式：",
            '{"workspace_path": "相对路径", "change_type": "edit|create|delete", '
            '"old_marker": "要替换的唯一文本", "new_content": "替换后的内容"}',
            "只输出 JSON 数组，不要其他文字。",
        ])
        return "\n".join(lines)
```

- [ ] **Step 4: 运行新测试验证通过**

```bash
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_opencode_analyzer.py::test_opencode_prompt_includes_history_when_prior_attempts_exist engineering/loop/controller/python/tests/test_opencode_analyzer.py::test_opencode_prompt_no_history_section_when_empty -v
```

Expected: 2 passed

- [ ] **Step 5: 运行 opencode analyzer 全部测试确保无回归**

```bash
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/test_opencode_analyzer.py -v
```

Expected: 全部 passed

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py engineering/loop/controller/python/tests/test_opencode_analyzer.py
git commit -m "功能(analyzer): G3 OpencodeAnalyzer prompt 注入历史尝试轨迹"
```

---

## Task 8: G3 + G8 — 全量回归

- [ ] **Step 1: 跑全量回归**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" pytest engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/connection/providers/rp5-serial/python/tests/ engineering/loop/connection/providers/adb/python/tests/ engineering/loop/deploy/python/tests/ engineering/loop/contracts/python/tests/ -q
```

Expected: 全部 passed（基线 602 + G8 新增 8 + G3 新增 6 = 616）

- [ ] **Step 2: 推送到远端**

```bash
git push origin main
```

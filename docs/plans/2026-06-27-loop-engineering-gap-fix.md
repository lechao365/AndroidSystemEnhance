# Loop Engineering 业界对标 Gap 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 loop engine 自动修复闭环（三层降级 analyzer + 知识积累）+ 清理旧架构 + 工程债修复

**Architecture:** 三层降级 analyzer（知识库 → 确定性规则 → opencode subprocess）注入 runtime_cli，DONE_SUCCESS 时自动归档到知识库（Reflexion 模式）。删除 v1 旧架构（control_cli/policy/state），消除 stages.py 全局状态，新增 human-in-loop 门和补丁格式升级。

**Tech Stack:** Python 3.11+, dataclasses, subprocess, PyYAML, pytest, opencode CLI

**Spec:** `docs/specs/2026-06-27-loop-engineering-gap-fix-design.md`

**Test Command:** `PYTHONPATH=engineering/loop/controller/python:engineering/loop/contracts/python:engineering/loop/core/python:engineering/loop/deploy/python:engineering/loop/connection/rp5-serial/python:engineering/loop/connection/adb/python python3 -m pytest engineering/ --tb=short -v`

---

## Task 依赖关系

```
T1 (KBEntry+fingerprint) ──→ T2 (KnowledgeBaseAnalyzer) ──→ T4 (ChainedAnalyzer)
                                                          ↗
T3 (OpencodeAnalyzer) ────────────────────────────────────/
T4 ──→ T5 (CLI 注入)
T1, T2 ──→ T6 (DONE_SUCCESS 归档)
T7 (lciod 规则) 独立
T8 (confidence 阈值) ──→ T9 (human-in-loop 门)
T10 (删 state/policy) ──→ T11 (删 control_cli) ──→ T12 (删 workflow + 文档)
T10 ──→ T13 (stages 全局状态消除)
T14 (patch_applier 升级) 独立
T1-T14 ──→ T15 (文档同步 + 全量回归)
```

## 文件结构

### 新增文件

| 文件 | 职责 |
|------|------|
| `engineering/loop/config/analyzer.yaml` | Analyzer 配置（opencode/kb/confidence/human_gate） |
| `engineering/loop/config/patch_knowledge_base.json` | 知识库（初始空） |
| `controller/python/tests/test_knowledge_base.py` | KB fingerprint + KnowledgeBaseAnalyzer 测试 |
| `controller/python/tests/test_opencode_analyzer.py` | OpencodeAnalyzer 测试（mock subprocess） |
| `controller/python/tests/test_chained_analyzer.py` | ChainedAnalyzer 三层降级测试 |
| `controller/python/tests/test_human_gate.py` | human-in-loop 门测试 |
| `controller/python/tests/test_legacy_removal.py` | 旧架构删除后导入验证 |

### 修改文件

| 文件 | 改动概述 |
|------|---------|
| `analyzer_protocol.py` | 新增 KBEntry/KnowledgeBaseAnalyzer/OpencodeAnalyzer/ChainedAnalyzer + 3 lciod 规则 + fingerprint 计算 + save_kb/update_kb + FileChange 字段扩展 |
| `runtime_cli.py` | 注入 ChainedAnalyzer + 新增 pending/approve/reject 子命令 + _build_analyzer + _load_analyzer_config |
| `engine.py` | DONE_SUCCESS 归档 + confidence 阈值检查 + pending_human_gate 退出 + KB 路径注入 |
| `stages.py` | StageContext + AnalysisRequest target/suite 补充 + policy 引用清理 |
| `patch_applier.py` | line_range + diff 模式 |
| `nodes.py` | patch_suggestion.json 新旧格式兼容 |
| `__init__.py` | 移除 policy/state/control_cli re-export |
| `loop_core/cli.py` | 移除 control 子命令挂载 |

### 删除文件

| 文件 | 原因 |
|------|------|
| `controller/python/loop_controller/control_cli.py` | v1 旧架构 |
| `controller/python/loop_controller/policy.py` | v1 旧架构 |
| `controller/python/loop_controller/state.py` | v1 旧架构 |
| `controller/python/tests/test_control_cli.py` | 随源码删除 |
| `controller/python/tests/test_policy.py` | 随源码删除 |
| `engineering/loop/workflows/lcview-adb-run/` | v1 手工编排（整个目录） |

---

## Phase 1：Analyzer 核心架构

### Task 1: KBEntry 数据结构 + fingerprint 计算

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py`（新增 KBEntry dataclass + _compute_fingerprint 函数 + AnalysisRequest 补充 target/suite 字段）
- Modify: `engineering/loop/controller/python/loop_controller/stages.py`（analyze_request_stage 构造 AnalysisRequest 时传入 target/suite）
- Test: `engineering/loop/controller/python/tests/test_knowledge_base.py`（新建）

- [ ] **Step 1: 写 fingerprint 计算的失败测试**

```python
# tests/test_knowledge_base.py
import re
from loop_controller.analyzer_protocol import AnalysisRequest, _compute_fingerprint

def test_fingerprint_stable_for_same_input():
    req = AnalysisRequest(
        session_id="s1", attempt_index=1, target="lciod",
        suite="features.lciod.end_to_end",
        failed_cases=[
            {"id": "HA-03", "failure_reason": "getStats field mismatch: read_bytes wrong"},
            {"id": "HA-07", "failure_reason": "readEvent incomplete"},
        ],
    )
    fp1 = _compute_fingerprint(req)
    fp2 = _compute_fingerprint(req)
    assert fp1 == fp2
    assert fp1.startswith("sha256:")

def test_fingerprint_differs_for_different_cases():
    req_a = AnalysisRequest(session_id="s1", attempt_index=1, target="lciod",
        suite="s", failed_cases=[{"id": "HA-03", "failure_reason": "x"}])
    req_b = AnalysisRequest(session_id="s1", attempt_index=1, target="lciod",
        suite="s", failed_cases=[{"id": "HA-07", "failure_reason": "y"}])
    assert _compute_fingerprint(req_a) != _compute_fingerprint(req_b)

def test_fingerprint_normalizes_path_and_whitespace():
    req1 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "error in /vendor/lechao/foo.cpp at line 10"}])
    req2 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "error  in  <path>  at  line  10"}])
    assert _compute_fingerprint(req1) == _compute_fingerprint(req2)

def test_fingerprint_case_insensitive():
    req1 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "Field Mismatch"}])
    req2 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "field mismatch"}])
    assert _compute_fingerprint(req1) == _compute_fingerprint(req2)

def test_fingerprint_order_independent():
    req1 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "B", "failure_reason": "x"}, {"id": "A", "failure_reason": "y"}])
    req2 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "A", "failure_reason": "y"}, {"id": "B", "failure_reason": "x"}])
    assert _compute_fingerprint(req1) == _compute_fingerprint(req2)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd engineering/loop/controller/python && python -m pytest tests/test_knowledge_base.py -v`
Expected: FAIL（`_compute_fingerprint` 不存在 / `target`/`suite` 参数不被接受）

- [ ] **Step 3: 实现 KBEntry + _compute_fingerprint + AnalysisRequest 扩展**

在 `analyzer_protocol.py` 中：

1. AnalysisRequest 补充 target/suite 字段：

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
    target: str = ""       # 新增
    suite: str = ""        # 新增
```

2. 文件头部追加 `import hashlib` / `import re`。

3. 在 `ScriptedAnalyzer` 之后追加 KBEntry + _compute_fingerprint：

```python
@dataclass
class KBEntry:
    """知识库条目：失败指纹 → 成功补丁映射。"""
    fingerprint: str
    fingerprint_components: dict = field(default_factory=dict)
    patch: list[dict] = field(default_factory=list)
    description: str = ""
    confidence: float = 0.95
    deploy_mode_hint: str = ""
    source_session: str = ""
    source_attempt: int = 0
    created_at: str = ""
    hit_count: int = 0
    last_hit_at: str = ""


def _compute_fingerprint(request: AnalysisRequest, reason_length: int = 80) -> str:
    """对 failed_cases 的 (case_id, failure_reason_signature) 集合做 SHA256。

    failure_reason_signature 取前 reason_length 字符，归一化空白和路径后小写化。
    """
    components = []
    for fc in sorted(request.failed_cases, key=lambda c: c.get("id", "")):
        case_id = fc.get("id", "")
        reason = (fc.get("failure_reason") or "")[:reason_length]
        reason = re.sub(r"\s+", " ", reason).strip().lower()
        reason = re.sub(r"/[\w/.-]+", "<path>", reason)
        components.append(f"{case_id}:{reason}")
    raw = f"{request.target}|{request.suite}|{'|'.join(components)}"
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
```

4. 在 `stages.py` 的 `analyze_request_stage` 中构造 AnalysisRequest 时传入 target/suite（从 session_dict 取）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd engineering/loop/controller/python && python -m pytest tests/test_knowledge_base.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py \
        engineering/loop/controller/python/tests/test_knowledge_base.py \
        engineering/loop/controller/python/loop_controller/stages.py
git commit -m "feat(loop-controller): KBEntry 数据结构与 fingerprint 计算

新增 KBEntry dataclass 和 _compute_fingerprint 函数。
AnalysisRequest 补充 target/suite 字段。
fingerprint 对 failed_cases 的 (case_id, reason_signature) 做 SHA256，
归一化路径和空白，支持跨 session 复用。"
```

---

### Task 2: KnowledgeBaseAnalyzer

**Files:**
- Modify: `analyzer_protocol.py`（新增 `KnowledgeBaseAnalyzer` 类）
- Test: `tests/test_knowledge_base.py`（扩充）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_knowledge_base.py 追加
import json
from loop_controller.analyzer_protocol import (
    KnowledgeBaseAnalyzer, KBEntry, AnalysisRequest, FileChange
)

def _make_kb_file(tmp_path, entries):
    kb = tmp_path / "kb.json"
    kb.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    return str(kb)

def test_kb_analyzer_loads_from_file(tmp_path):
    entry = {
        "fingerprint": "sha256:abc",
        "patch": [{"workspace_path": "foo.c", "change_type": "edit",
                    "old_marker": "x", "new_content": "y"}],
        "description": "test entry",
        "deploy_mode_hint": "PUSH_SINGLE",
    }
    kb_path = _make_kb_file(tmp_path, [entry])
    analyzer = KnowledgeBaseAnalyzer(kb_path)
    assert len(analyzer._kb) == 1

def test_kb_analyzer_hit_returns_patch(tmp_path):
    entry = {
        "fingerprint": "sha256:abc",
        "patch": [{"workspace_path": "foo.c", "change_type": "edit",
                    "old_marker": "x", "new_content": "y"}],
        "description": "test",
    }
    kb_path = _make_kb_file(tmp_path, [entry])
    analyzer = KnowledgeBaseAnalyzer(kb_path)
    analyzer._compute_fingerprint = lambda r: "sha256:abc"
    req = AnalysisRequest(session_id="s", attempt_index=1)
    suggestion = analyzer.analyze(req)
    assert len(suggestion.target_files) == 1
    assert suggestion.target_files[0].workspace_path == "foo.c"
    assert suggestion.confidence == 0.98

def test_kb_analyzer_miss_returns_empty(tmp_path):
    kb_path = _make_kb_file(tmp_path, [])
    analyzer = KnowledgeBaseAnalyzer(kb_path)
    req = AnalysisRequest(session_id="s", attempt_index=1,
                          failed_cases=[{"id": "C1", "failure_reason": "x"}])
    suggestion = analyzer.analyze(req)
    assert suggestion.target_files == []
    assert suggestion.confidence == 0.0

def test_kb_analyzer_missing_file_returns_empty_list(tmp_path):
    analyzer = KnowledgeBaseAnalyzer(str(tmp_path / "nonexistent.json"))
    assert analyzer._kb == []

def test_kb_analyzer_corrupt_json_returns_empty_list(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    analyzer = KnowledgeBaseAnalyzer(str(bad))
    assert analyzer._kb == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_knowledge_base.py -v -k "kb_analyzer"`
Expected: FAIL（`KnowledgeBaseAnalyzer` 不存在）

- [ ] **Step 3: 实现 KnowledgeBaseAnalyzer**

在 `analyzer_protocol.py` 追加（KBEntry 之后）：

```python
import dataclasses

class KnowledgeBaseAnalyzer(LlmAnalyzer):
    """从 patch_knowledge_base.json 加载历史成功补丁，按 fingerprint 匹配。"""

    def __init__(self, kb_path: str, hit_confidence: float = 0.98):
        self._kb_path = kb_path
        self._hit_confidence = hit_confidence
        self._kb: list[KBEntry] = self._load_kb(kb_path)

    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        fingerprint = self._compute_fingerprint(request)
        for entry in self._kb:
            if entry.fingerprint == fingerprint:
                patches = [FileChange(**p) for p in entry.patch]
                return PatchSuggestion(
                    target_files=patches,
                    rationale=f"知识库命中：{entry.description}",
                    confidence=self._hit_confidence,
                    deploy_mode_hint=entry.deploy_mode_hint,
                )
        return PatchSuggestion(target_files=[], confidence=0.0)

    @staticmethod
    def _load_kb(kb_path: str) -> list[KBEntry]:
        if not kb_path or not os.path.isfile(kb_path):
            return []
        try:
            data = json.loads(Path(kb_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        entries = []
        for e in data.get("entries", []):
            try:
                entries.append(KBEntry(**e))
            except TypeError:
                continue
        return entries

    @staticmethod
    def _compute_fingerprint(request: AnalysisRequest) -> str:
        return _compute_fingerprint(request)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_knowledge_base.py -v -k "kb_analyzer"`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): KnowledgeBaseAnalyzer 加载 KB 并按 fingerprint 匹配"
```

---

### Task 3: OpencodeAnalyzer

**Files:**
- Modify: `analyzer_protocol.py`（新增 `OpencodeAnalyzer` 类）
- Test: `tests/test_opencode_analyzer.py`（新建）

- [ ] **Step 1: 写失败测试（mock subprocess）**

```python
# tests/test_opencode_analyzer.py
import json
import subprocess
from unittest.mock import patch, MagicMock
from loop_controller.analyzer_protocol import OpencodeAnalyzer, AnalysisRequest

def _mock_opencode_output(patches: list[dict]) -> str:
    """模拟 opencode run --format json 的输出。"""
    return json.dumps([
        {"type": "assistant", "content": json.dumps(patches)}
    ])

def test_opencode_analyzer_parses_valid_output(tmp_path):
    patches = [{"workspace_path": "foo.c", "change_type": "edit",
                "old_marker": "x", "new_content": "y"}]
    mock_output = _mock_opencode_output(patches)
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path), timeout=10)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert len(suggestion.target_files) == 1
    assert suggestion.target_files[0].workspace_path == "foo.c"

def test_opencode_analyzer_timeout_returns_empty(tmp_path):
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path), timeout=1)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert suggestion.target_files == []
    assert suggestion.confidence == 0.0

def test_opencode_analyzer_nonzero_exit_returns_empty(tmp_path):
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert suggestion.target_files == []

def test_opencode_analyzer_no_json_in_output_returns_empty(tmp_path):
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="no json here")
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert suggestion.target_files == []

def test_opencode_prompt_includes_evidence_and_failed_cases(tmp_path):
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    req = AnalysisRequest(
        session_id="s", attempt_index=1, target="lciod",
        failed_cases=[{"id": "HA-03", "failure_reason": "field mismatch"}],
        evidence_bundle_path="/tmp/eb.json",
    )
    prompt = analyzer._build_prompt(req)
    assert "HA-03" in prompt
    assert "field mismatch" in prompt
    assert "/tmp/eb.json" in prompt
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_opencode_analyzer.py -v`
Expected: FAIL（`OpencodeAnalyzer` 不存在）

- [ ] **Step 3: 实现 OpencodeAnalyzer**

在 `analyzer_protocol.py` 追加：

```python
import subprocess
import tempfile

class OpencodeAnalyzer(LlmAnalyzer):
    """通过 subprocess 调 opencode run，让 LLM 生成补丁。"""

    def __init__(self, workspace_root: str, model: str = "",
                 timeout: int = 300, binary: str = "opencode"):
        self._workspace_root = workspace_root
        self._model = model
        self._timeout = timeout
        self._binary = binary

    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        try:
            prompt = self._build_prompt(request)
            req_file = self._write_request_file(request)
            result = self._invoke_opencode(prompt, req_file)
            return self._parse_suggestion(result)
        except Exception:
            return PatchSuggestion(target_files=[], confidence=0.0,
                                   rationale="opencode analyzer 失败")

    def _build_prompt(self, request: AnalysisRequest) -> str:
        lines = [
            "你是代码修复助手。以下测试用例失败了，请分析根因并生成修复补丁。",
            "",
            f"Target: {request.target}",
            f"Suite: {request.suite}",
            "",
            "## 失败用例",
        ]
        for fc in request.failed_cases:
            lines.append(f"- {fc.get('id', '?')}: {fc.get('failure_reason', '?')}")
        if request.evidence_bundle_path:
            lines.append(f"\n## EvidenceBundle\n路径: {request.evidence_bundle_path}")
            lines.append("（可读取该文件获取完整上下文）")
        if request.workspace_diff_so_far:
            lines.append("\n## 当前 workspace diff（前 1000 字符）")
            lines.append(request.workspace_diff_so_far[:1000])
        lines.extend([
            "",
            "## 输出要求",
            "输出严格 JSON 数组，每个元素格式：",
            '{"workspace_path": "相对路径", "change_type": "edit|create|delete", '
            '"old_marker": "要替换的唯一文本", "new_content": "替换后的内容"}',
            "只输出 JSON 数组，不要其他文字。",
        ])
        return "\n".join(lines)

    def _write_request_file(self, request: AnalysisRequest) -> str:
        import os as _os
        fd, path = tempfile.mkstemp(suffix=".json", prefix="analyzer_req_")
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({
                "failed_cases": request.failed_cases,
                "evidence_bundle_path": request.evidence_bundle_path,
                "target": request.target,
                "suite": request.suite,
            }, f, ensure_ascii=False)
        return path

    def _invoke_opencode(self, prompt: str, req_file: str) -> str:
        cmd = [self._binary, "run", "--format", "json"]
        if self._model:
            cmd.extend(["-m", self._model])
        cmd.extend(["-f", req_file, prompt])
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=self._timeout, cwd=self._workspace_root,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"opencode exited {result.returncode}: {result.stderr[:200]}")
        return result.stdout

    def _parse_suggestion(self, output: str) -> PatchSuggestion:
        try:
            events = json.loads(output)
            if isinstance(events, list):
                for ev in reversed(events):
                    if ev.get("type") == "assistant":
                        content = ev.get("content", "")
                        patches = json.loads(content)
                        if isinstance(patches, list) and patches:
                            changes = [FileChange(**p) for p in patches]
                            return PatchSuggestion(
                                target_files=changes, confidence=0.8,
                                rationale="opencode LLM 生成",
                            )
        except (json.JSONDecodeError, TypeError):
            pass
        return PatchSuggestion(target_files=[], confidence=0.0)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_opencode_analyzer.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): OpencodeAnalyzer 通过 subprocess 调 opencode run 生成补丁"
```

---

### Task 4: ChainedAnalyzer

**Files:**
- Modify: `analyzer_protocol.py`（新增 `ChainedAnalyzer` 类）
- Test: `tests/test_chained_analyzer.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_chained_analyzer.py
from loop_controller.analyzer_protocol import (
    ChainedAnalyzer, PatchSuggestion, FileChange, LlmAnalyzer, AnalysisRequest
)

class _StubAnalyzer(LlmAnalyzer):
    def __init__(self, patches=None, name="stub"):
        self._patches = patches or []
        self._name = name
        self.called = False
    def analyze(self, request):
        self.called = True
        if self._patches:
            return PatchSuggestion(target_files=self._patches, confidence=0.9)
        return PatchSuggestion(target_files=[], confidence=0.0)

def test_chained_returns_first_non_empty():
    p1 = [FileChange(workspace_path="a.c", old_marker="x", new_content="y")]
    layer1 = _StubAnalyzer(patches=[], name="empty")
    layer2 = _StubAnalyzer(patches=p1, name="hit")
    chain = ChainedAnalyzer([layer1, layer2])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert layer1.called
    assert not layer2.called
    assert len(result.target_files) == 1

def test_chained_falls_through_all_empty():
    l1 = _StubAnalyzer(patches=[])
    l2 = _StubAnalyzer(patches=[])
    chain = ChainedAnalyzer([l1, l2])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert result.target_files == []
    assert l1.called and l2.called

def test_chained_rationale_includes_layer_name():
    p = [FileChange(workspace_path="a.c")]
    chain = ChainedAnalyzer([_StubAnalyzer(patches=p, name="TestAnalyzer")])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert "_StubAnalyzer" in result.rationale

def test_chained_skips_layer_that_raises():
    class _CrashAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            raise RuntimeError("boom")
    p = [FileChange(workspace_path="a.c")]
    chain = ChainedAnalyzer([_CrashAnalyzer(), _StubAnalyzer(patches=p)])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert len(result.target_files) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_chained_analyzer.py -v`
Expected: FAIL（`ChainedAnalyzer` 不存在）

- [ ] **Step 3: 实现 ChainedAnalyzer**

在 `analyzer_protocol.py` 追加：

```python
class ChainedAnalyzer(LlmAnalyzer):
    """三层降级：KB → 规则 → opencode。首个非空产出即返回。"""

    def __init__(self, layers: list[LlmAnalyzer]):
        self._layers = layers

    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        for layer in self._layers:
            try:
                suggestion = layer.analyze(request)
            except Exception:
                continue
            if suggestion.target_files:
                suggestion.rationale = f"[{type(layer).__name__}] {suggestion.rationale}"
                return suggestion
        return PatchSuggestion(
            target_files=[], confidence=0.0,
            rationale="三层 analyzer 均无产出",
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_chained_analyzer.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): ChainedAnalyzer 三层降级编排（KB→规则→opencode）"
```

---

## Phase 2：CLI 注入

### Task 5: runtime_cli 注入 ChainedAnalyzer + config/analyzer.yaml

**Files:**
- Create: `engineering/loop/config/analyzer.yaml`
- Create: `engineering/loop/config/patch_knowledge_base.json`（初始空）
- Modify: `engineering/loop/controller/python/loop_controller/runtime_cli.py`（`_handle_run` / `_handle_resume` 注入 ChainedAnalyzer + `_build_analyzer` + `_load_analyzer_config`）
- Test: `engineering/loop/controller/python/tests/test_runtime_cli.py`（扩充）

- [ ] **Step 1: 写失败测试（验证注入）**

```python
# tests/test_runtime_cli.py 追加
import json
from unittest.mock import patch, MagicMock
from loop_controller.runtime_cli import _handle_run

def test_run_injects_chained_analyzer(tmp_path):
    session = tmp_path / "session.json"
    session_data = {
        "session_id": "test-s1", "workflow_id": "runtime", "target": "lciod",
        "suite": "features.lciod.common", "max_attempts": 1, "current_attempt": 0,
        "status": "PENDING", "latest_failure_code": "NONE", "attempts": [],
        "artifacts_dir": str(tmp_path),
    }
    session.write_text(json.dumps(session_data), encoding="utf-8")

    captured = {}
    def fake_init(self, *args, **kwargs):
        captured["analyzer"] = kwargs.get("analyzer")
        from loop_contracts.models import RuntimeTerminalState
        self._state = MagicMock(terminal_state=RuntimeTerminalState.DONE_SUCCESS)
        self.run = lambda max_iterations=100: self._state

    with patch("loop_controller.runtime.engine.LoopRuntime.__init__", fake_init):
        args = MagicMock(session=str(session), adb_endpoint="")
        _handle_run(args)
    assert captured["analyzer"] is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_runtime_cli.py::test_run_injects_chained_analyzer -v`
Expected: FAIL（analyzer 未注入，为 None）

- [ ] **Step 3: 创建配置文件 + 修改 runtime_cli**

创建 `engineering/loop/config/analyzer.yaml`：

```yaml
opencode:
  binary: "opencode"
  model: ""
  timeout: 300
  format: "json"

knowledge_base:
  path: "config/patch_knowledge_base.json"
  max_entries: 100
  fingerprint_reason_length: 80

confidence:
  threshold: 0.7
  rule_match: 0.95
  kb_match: 0.98

human_gate:
  enabled: true
  triggers:
    - low_confidence
    - kernel_patch
    - dd_boot_reboot
```

创建 `engineering/loop/config/patch_knowledge_base.json`：

```json
{
  "version": 1,
  "entries": []
}
```

修改 `runtime_cli.py`：

1. 文件头部追加 `import os`。
2. 新增 `_load_analyzer_config`（读取 analyzer.yaml，PyYAML 不可用时返回空 dict）。
3. 新增 `_build_analyzer` 返回 `(ChainedAnalyzer, kb_path, confidence_threshold)` 三元组。
4. `_handle_run` 和 `_handle_resume` 中调用 `_build_analyzer()`，注入 analyzer + kb_path + confidence_threshold。

关键代码：

```python
def _load_analyzer_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "analyzer.yaml"
    if not config_path.is_file():
        return {}
    try:
        import yaml
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

def _build_analyzer():
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer, KnowledgeBaseAnalyzer, ScriptedAnalyzer, OpencodeAnalyzer
    )
    cfg = _load_analyzer_config()
    kb_cfg = cfg.get("knowledge_base", {})
    oai_cfg = cfg.get("opencode", {})
    conf_cfg = cfg.get("confidence", {})
    loop_config_dir = Path(__file__).resolve().parent.parent.parent / "config"
    kb_rel = kb_cfg.get("path", "patch_knowledge_base.json")
    kb_path = str(loop_config_dir / Path(kb_rel).name)
    ws_root = os.environ.get("AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))
    layers = [
        KnowledgeBaseAnalyzer(kb_path, hit_confidence=conf_cfg.get("kb_match", 0.98)),
        ScriptedAnalyzer(),
        OpencodeAnalyzer(
            workspace_root=ws_root,
            model=oai_cfg.get("model", ""),
            timeout=oai_cfg.get("timeout", 300),
            binary=oai_cfg.get("binary", "opencode"),
        ),
    ]
    return ChainedAnalyzer(layers), kb_path, conf_cfg.get("threshold", 0.7)
```

`_handle_run` 修改：

```python
def _handle_run(args):
    try:
        session, ts = _load_session(args.session)
        if ts != RuntimeTerminalState.NONE:
            print(f"terminal_state={ts.value}")
            return 0 if ts == RuntimeTerminalState.DONE_SUCCESS else 1
        serial_sh = _resolve_serial_shell()
        analyzer, kb_path, conf_threshold = _build_analyzer()
        rt = LoopRuntime(session, _CASES_DIR, _DEVICE_PROFILE,
                         adb_endpoint=args.adb_endpoint,
                         initial_terminal_state=ts,
                         serial_shell_provider=serial_sh,
                         analyzer=analyzer)
        rt._kb_path = kb_path
        rt._confidence_threshold = conf_threshold
        state = rt.run()
        print(f"terminal_state={state.terminal_state.value}")
        if state.terminal_state == RuntimeTerminalState.DONE_SUCCESS:
            return 0
        return 1
    except Exception as e:
        print(f"RUNTIME_FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        _persist_failure(args.session, e)
        return 2
```

`_handle_resume` 同理注入。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_runtime_cli.py::test_run_injects_chained_analyzer -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): runtime_cli 注入 ChainedAnalyzer + analyzer.yaml 配置"
```

---

## Phase 3：知识积累

### Task 6: DONE_SUCCESS 归档到知识库

**Files:**
- Modify: `engine.py`（新增 `_archive_to_knowledge_base` + `_kb_path` 属性 + DONE_SUCCESS 分支调用）
- Modify: `analyzer_protocol.py`（新增 `save_kb` / `update_kb` 函数）
- Test: `tests/test_runtime_engine.py`（扩充）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_runtime_engine.py 追加
import json
from pathlib import Path
from loop_controller.runtime.engine import LoopRuntime
from loop_contracts.models import LoopSession

def test_done_success_archives_to_kb(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch_data = [{"workspace_path": "foo.c", "change_type": "edit",
                   "old_marker": "x", "new_content": "y"}]
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps({"patches": patch_data, "confidence": 0.9}), encoding="utf-8")
    kb_path = str(tmp_path / "kb.json")

    session = LoopSession(
        session_id="lciod-test", target="lciod",
        suite="features.lciod.end_to_end", max_attempts=3,
        current_attempt=1, artifacts_dir=str(artifacts),
        attempts=[{
            "verify": {
                "case_results": [{"id": "HA-03", "status": "fail"}],
                "failed_count": 1,
                "failed_cases": [{"id": "HA-03", "failure_reason": "field mismatch"}],
            },
            "patch_applied": {"patch_hash": "abc"},
        }],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._kb_path = kb_path
    rt._archive_to_knowledge_base()

    kb = json.loads(Path(kb_path).read_text())
    assert len(kb["entries"]) == 1
    assert kb["entries"][0]["source_session"] == "lciod-test"

def test_archive_does_not_duplicate_same_fingerprint(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch1 = [{"workspace_path": "a.c", "old_marker": "x", "new_content": "y"}]
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps({"patches": patch1, "confidence": 0.9}), encoding="utf-8")
    kb_path = str(tmp_path / "kb.json")

    session = LoopSession(
        session_id="s1", target="lciod", suite="s",
        max_attempts=3, current_attempt=1, artifacts_dir=str(artifacts),
        attempts=[{"verify": {"failed_cases": [{"id": "C1", "failure_reason": "err"}]}}],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._kb_path = kb_path
    rt._archive_to_knowledge_base()
    rt._archive_to_knowledge_base()

    kb = json.loads(Path(kb_path).read_text())
    assert len(kb["entries"]) == 1

def test_archive_silent_failure_on_missing_patch(tmp_path):
    kb_path = str(tmp_path / "kb.json")
    session = LoopSession(
        session_id="s1", target="t", suite="s", max_attempts=1,
        current_attempt=0, artifacts_dir=str(tmp_path), attempts=[],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._kb_path = kb_path
    rt._archive_to_knowledge_base()
    assert not Path(kb_path).exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_runtime_engine.py -v -k "archive"`
Expected: FAIL（`_archive_to_knowledge_base` 不存在）

- [ ] **Step 3: 实现归档逻辑**

`analyzer_protocol.py` 新增 `save_kb` / `update_kb`（KBEntry 之后）：

```python
import time as _time

def save_kb(kb_path: str, entries: list[KBEntry], max_entries: int = 100) -> None:
    """保存知识库，超限时淘汰 hit_count 最低的条目。"""
    if len(entries) > max_entries:
        entries.sort(key=lambda e: e.hit_count, reverse=True)
        entries = entries[:max_entries]
    data = {"version": 1, "entries": [dataclasses.asdict(e) for e in entries]}
    os.makedirs(os.path.dirname(kb_path) or ".", exist_ok=True)
    Path(kb_path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def update_kb(kb_path: str, fingerprint: str, fingerprint_components: dict,
              patch: list[dict], description: str, deploy_mode_hint: str,
              source_session: str, source_attempt: int, max_entries: int = 100) -> None:
    """更新知识库：同 fingerprint 更新，否则追加。"""
    analyzer = KnowledgeBaseAnalyzer(kb_path)
    entries = analyzer._kb
    now = _time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    existing = next((e for e in entries if e.fingerprint == fingerprint), None)
    if existing:
        existing.patch = patch
        existing.description = description
        existing.deploy_mode_hint = deploy_mode_hint
        existing.source_session = source_session
        existing.source_attempt = source_attempt
        existing.created_at = now
    else:
        entries.append(KBEntry(
            fingerprint=fingerprint,
            fingerprint_components=fingerprint_components,
            patch=patch, description=description,
            deploy_mode_hint=deploy_mode_hint,
            source_session=source_session, source_attempt=source_attempt,
            created_at=now,
        ))
    save_kb(kb_path, entries, max_entries)
```

`engine.py` 改动：

1. `__init__` 新增 `self._kb_path: str = ""`（L53 之后）
2. DONE_SUCCESS 分支新增归档调用（在 `_cleanup_all_worktrees()` 之前）
3. 新增 `_archive_to_knowledge_base` 方法

```python
# __init__ 新增
self._kb_path: str = ""

# DONE_SUCCESS 分支
elif next_nk == NodeKind.DONE_SUCCESS:
    self._state.terminal_state = RuntimeTerminalState.DONE_SUCCESS
    self._archive_to_knowledge_base()
    self._cleanup_all_worktrees()

def _archive_to_knowledge_base(self) -> None:
    """DONE_SUCCESS 时归档成功补丁到知识库。"""
    if not self._kb_path:
        return
    try:
        from loop_controller.analyzer_protocol import (
            _compute_fingerprint, update_kb, AnalysisRequest,
        )
        patch_path = os.path.join(
            self._session.artifacts_dir, "patch_suggestion.json")
        if not os.path.isfile(patch_path):
            return
        raw = json.loads(Path(patch_path).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "patches" in raw:
            patches = raw["patches"]
        elif isinstance(raw, list):
            patches = raw
        else:
            return
        if not isinstance(patches, list) or not patches:
            return
        latest = self._session.attempts[-1] if self._session.attempts else {}
        failed_cases = latest.get("verify", {}).get("failed_cases", [])
        if not failed_cases:
            case_results = latest.get("verify", {}).get("case_results", [])
            failed_cases = [c for c in case_results
                            if c.get("status") in ("fail", "error")]
        req = AnalysisRequest(
            session_id=self._session.session_id,
            attempt_index=self._session.current_attempt,
            failed_cases=failed_cases,
            target=self._session.target,
            suite=self._session.suite,
        )
        fp = _compute_fingerprint(req)
        update_kb(
            self._kb_path, fp, {}, patches,
            description=f"自动归档 from {self._session.session_id}",
            deploy_mode_hint="",
            source_session=self._session.session_id,
            source_attempt=self._session.current_attempt,
        )
    except Exception:
        pass
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_runtime_engine.py -v -k "archive"`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): DONE_SUCCESS 时自动归档补丁到知识库（Reflexion 模式）"
```

---

## Phase 4：规则库扩充

### Task 7: lciod 3 bug 确定性规则

**Files:**
- Modify: `analyzer_protocol.py`（新增 3 条规则函数 + 注册到 `_RULES`）
- Test: `tests/test_analyzer_protocol.py`（扩充）

> **重要前置**：`old_marker` 的确切文本必须对照 `~/workspace/aosp/vendor/lechao/services/lechao_lciod/` 实际源码。实施前先读取这些文件确认 marker 文本。下面的 marker 是基于设计文档推断的占位，实施时替换为实际代码。

- [ ] **Step 0: 读取实际源码确认 marker**

```bash
# 读取 HAL 源码
ls ~/workspace/aosp/vendor/lechao/services/lechao_lciod/
# 找到 service.cpp / daemon.cpp 中的目标代码段
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_analyzer_protocol.py 追加
from loop_controller.analyzer_protocol import (
    ScriptedAnalyzer, AnalysisRequest,
    _rule_lciod_hal_field_inversion,
    _rule_lciod_daemon_formula_error,
    _rule_lciod_hal_readdrain_missing,
)

def test_hal_field_inversion_detects_swapped_bytes():
    case = {
        "id": "HA-05",
        "failure_reason": "json_field read_bytes expected>0 but got 0, "
                          "write_bytes mismatch: expected 1024 got 2048",
        "command": "fault-verify stats --json",
    }
    changes = _rule_lciod_hal_field_inversion(case)
    assert changes is not None
    assert len(changes) > 0

def test_daemon_formula_error_detects_negative_rate():
    case = {
        "id": "DA-07",
        "failure_reason": "json_field getAverageRate expected ge 0 but got -1.5",
        "command": "fault-verify stats --json",
    }
    changes = _rule_lciod_daemon_formula_error(case)
    assert changes is not None

def test_hal_readdrain_missing_detects_incomplete_events():
    case = {
        "id": "HA-09",
        "failure_reason": "readEvent returned 0 events after dd write, expected > 0",
        "command": "fault-verify event --read --count 5 --json",
    }
    changes = _rule_lciod_hal_readdrain_missing(case)
    assert changes is not None

def test_lciod_rules_no_false_positive_on_unrelated_failure():
    case = {
        "id": "HA-01",
        "failure_reason": "service vendor.lechao.lciod not found",
        "command": "adb shell service list",
    }
    assert _rule_lciod_hal_field_inversion(case) is None
    assert _rule_lciod_daemon_formula_error(case) is None
    assert _rule_lciod_hal_readdrain_missing(case) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_analyzer_protocol.py -v -k "lciod"`
Expected: FAIL（3 个规则函数不存在）

- [ ] **Step 3: 实现 3 条规则**

在 `analyzer_protocol.py` 中 `_rule_fv_stdout_pollution` 之后追加 3 个规则函数，并更新 `_RULES` 列表。

路径常量（实施时按实际 workspace 结构调整）：

```python
_LCIOD_HAL_PATH = "vendor/lechao/services/lechao_lciod/service.cpp"
_LCIOD_DAEMON_PATH = "vendor/lechao/services/lechao_lciod/daemon.cpp"
```

规则实现（`old_marker` 需替换为实际源码文本）：

```python
def _rule_lciod_hal_field_inversion(case: dict) -> list[FileChange] | None:
    """LCIOD HAL getStats 字段反转：read_bytes 和 write_bytes 值互换。"""
    reason = (case.get("failure_reason") or "").lower()
    if "read_bytes" not in reason or "write_bytes" not in reason:
        return None
    if "mismatch" not in reason and "expected" not in reason:
        return None
    return [FileChange(
        workspace_path=_LCIOD_HAL_PATH,
        change_type="edit",
        old_marker="stats.read_bytes = raw.read_bytes;\n    stats.write_bytes = raw.write_bytes;",
        new_content="stats.write_bytes = raw.read_bytes;\n    stats.read_bytes = raw.write_bytes;",
    )]

def _rule_lciod_daemon_formula_error(case: dict) -> list[FileChange] | None:
    """LCIOD Daemon getAverageRate 公式错误。"""
    reason = (case.get("failure_reason") or "").lower()
    if "getaveragerate" not in reason:
        return None
    return [FileChange(
        workspace_path=_LCIOD_DAEMON_PATH,
        change_type="edit",
        old_marker="double rate = static_cast<double>(bytes) / (interval_ns / 1000000000.0);",
        new_content="double rate = static_cast<double>(bytes) * 1000000000.0 / interval_ns;",
    )]

def _rule_lciod_hal_readdrain_missing(case: dict) -> list[FileChange] | None:
    """LCIOD HAL readEvent 排空遗漏。"""
    reason = (case.get("failure_reason") or "").lower()
    if "readevent" not in reason:
        return None
    if "0 events" not in reason and "incomplete" not in reason:
        return None
    return [FileChange(
        workspace_path=_LCIOD_HAL_PATH,
        change_type="edit",
        old_marker="return events;",
        new_content="// 排空缓冲区\n    while (kernel_buf.has_data()) {\n        events.push_back(kernel_buf.read_one());\n    }\n    return events;",
    )]

_RULES = [
    _rule_fv_stdout_pollution,
    _rule_lciod_hal_field_inversion,
    _rule_lciod_daemon_formula_error,
    _rule_lciod_hal_readdrain_missing,
]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_analyzer_protocol.py -v -k "lciod"`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): lciod 3 bug 确定性规则（HAL 字段反转/Daemon 公式/readEvent 排空）"
```

---

## Phase 5：human-in-loop 门

### Task 8: confidence 阈值检查 + patch_suggestion.json 格式扩展

**Files:**
- Modify: `engine.py`（新增 `_confidence_threshold` + `_read_suggestion_meta` + APPLY_PATCH 阈值检查 + `__init__` 新增 `_confidence_threshold`）
- Modify: `engine.py` `_execute_wait_analyzer_patch`（落盘时写入新格式）
- Modify: `nodes.py` `node_apply_patch`（读取时兼容新旧格式）
- Test: `tests/test_runtime_engine.py`（扩充）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_runtime_engine.py 追加
from loop_controller.runtime.types import NodeKind

def test_low_confidence_triggers_human_gate(tmp_path):
    """confidence < threshold 时触发 pending_human_gate 而非自动 apply。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "foo.c", "change_type": "edit",
                      "old_marker": "x", "new_content": "y"}],
        "confidence": 0.3,
        "rationale": "low confidence test",
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")

    session = LoopSession(
        session_id="test", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
        attempts=[],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt._execute_current_node()
    assert rt._state.pending_human_gate is True
    assert rt._state.node_status == "LOW_CONFIDENCE"
    assert rt._state.terminal_state == RuntimeTerminalState.NONE

def test_high_confidence_proceeds_to_apply(tmp_path):
    """confidence >= threshold 时不触发 gate。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "foo.c", "change_type": "edit",
                      "old_marker": "x", "new_content": "y"}],
        "confidence": 0.9,
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")

    session = LoopSession(
        session_id="test", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
        attempts=[],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt._execute_current_node()
    assert not rt._state.pending_human_gate

def test_old_format_list_treated_as_high_confidence(tmp_path):
    """旧格式 [FileChange] 列表视为 confidence=1.0，不触发 gate。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch_list = [{"workspace_path": "foo.c", "old_marker": "x", "new_content": "y"}]
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(patch_list), encoding="utf-8")

    session = LoopSession(
        session_id="test", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
        attempts=[],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt._execute_current_node()
    assert not rt._state.pending_human_gate
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_runtime_engine.py -v -k "confidence"`
Expected: FAIL（`_read_suggestion_meta` / `_confidence_threshold` 不存在）

- [ ] **Step 3: 实现**

`engine.py` `__init__` 新增：

```python
self._confidence_threshold: float = 0.7
```

`engine.py` 新增 `_read_suggestion_meta`：

```python
def _read_suggestion_meta(self) -> dict | None:
    """读取 patch_suggestion.json 的元数据（confidence/rationale）。"""
    patch_path = os.path.join(self._session.artifacts_dir, "patch_suggestion.json")
    if not os.path.isfile(patch_path):
        return None
    try:
        data = json.loads(Path(patch_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(data, dict) and "patches" in data:
        return data
    if isinstance(data, list):
        return {"patches": data, "confidence": 1.0}
    return None
```

`engine.py` APPLY_PATCH 分支开头（L124 `elif node == NodeKind.APPLY_PATCH.value:` 之后、检查 patch_path 存在性之前）新增：

```python
# confidence 阈值检查
suggestion_meta = self._read_suggestion_meta()
if suggestion_meta:
    conf = suggestion_meta.get("confidence", 1.0)
    if conf < self._confidence_threshold:
        self._state.node_status = "LOW_CONFIDENCE"
        self._state.pending_human_gate = True
        self._checkpoint(
            f"confidence {conf} below threshold {self._confidence_threshold}",
            FailureCode.NONE,
        )
        return
```

`engine.py` `_execute_wait_analyzer_patch` 落盘改造（L399-404 替换）：

```python
if suggestion.target_files:
    patch_data = {
        "patches": [dataclasses.asdict(fc) for fc in suggestion.target_files],
        "confidence": suggestion.confidence,
        "rationale": suggestion.rationale,
    }
    Path(patch_path).write_text(
        json.dumps(patch_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
```

`nodes.py` `node_apply_patch` 读取兼容（L86-95 替换）：

```python
raw = json.loads(Path(patch_path).read_text(encoding="utf-8"))
if isinstance(raw, dict) and "patches" in raw:
    raw_changes = raw["patches"]
elif isinstance(raw, list):
    raw_changes = raw
else:
    return {"status": "PATCH_INVALID", "failure_code": FailureCode.PATCH_REJECTED,
            "error": "patch_suggestion.json format unknown"}
changes = [FileChange(**c) for c in raw_changes]
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_runtime_engine.py -v -k "confidence"`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): confidence 阈值检查 + patch_suggestion.json 新旧格式兼容"
```

---

### Task 9: human-in-loop 门（pending/approve/reject CLI）

**Files:**
- Modify: `engine.py`（`run()` 检查 `pending_human_gate` 退出）
- Modify: `runtime_cli.py`（新增 `pending`/`approve`/`reject` 子命令）
- Test: `tests/test_human_gate.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_human_gate.py
import json
from pathlib import Path
from loop_controller.runtime.engine import LoopRuntime
from loop_controller.runtime.types import NodeKind
from loop_contracts.models import LoopSession, RuntimeTerminalState

def test_pending_human_gate_stops_run_loop(tmp_path):
    """pending_human_gate=True 时 run() 退出且不设终态。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "f.c", "old_marker": "x", "new_content": "y"}],
        "confidence": 0.3,
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")
    session = LoopSession(
        session_id="test", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt.run(max_iterations=1)
    assert rt._state.pending_human_gate is True
    assert rt._state.terminal_state == RuntimeTerminalState.NONE

def test_pending_command_shows_gate_info(tmp_path):
    """le runtime pending 显示待确认信息。"""
    from loop_controller.runtime_cli import _handle_pending
    session_data = {
        "session_id": "s1", "current_node": "APPLY_PATCH",
        "node_status": "LOW_CONFIDENCE", "pending_human_gate": True,
        "artifacts_dir": str(tmp_path),
    }
    sp = tmp_path / "session.json"
    sp.write_text(json.dumps(session_data), encoding="utf-8")
    args = type("A", (), {"session": str(sp)})()
    rc = _handle_pending(args)
    assert rc == 0

def test_reject_sets_escalate_terminal(tmp_path):
    """le runtime reject 设终态 ESCALATE_HUMAN。"""
    from loop_controller.runtime_cli import _handle_reject
    session_data = {
        "session_id": "s1", "pending_human_gate": True,
        "artifacts_dir": str(tmp_path),
    }
    sp = tmp_path / "session.json"
    sp.write_text(json.dumps(session_data), encoding="utf-8")
    args = type("A", (), {"session": str(sp)})()
    rc = _handle_reject(args)
    assert rc == 1
    data = json.loads(sp.read_text())
    assert data["terminal_state"] == "ESCALATE_HUMAN"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_human_gate.py -v`
Expected: FAIL（`_handle_pending` / `_handle_reject` 不存在 / run 不检查 gate）

- [ ] **Step 3: 实现**

`engine.py` `run()` 修改（在 `_execute_current_node()` 之后、终态检查之前）：

```python
def run(self, max_iterations: int = 100) -> RuntimeState:
    iterations = 0
    while self._state.terminal_state == RuntimeTerminalState.NONE:
        iterations += 1
        if iterations > max_iterations:
            self._state.terminal_state = RuntimeTerminalState.DONE_FAILURE
            self._state.transition_reason = f"max_iterations({max_iterations}) exceeded"
            break
        self._execute_current_node()
        if self._state.pending_human_gate:
            self._persist_session()
            return self._state
        if self._state.terminal_state != RuntimeTerminalState.NONE:
            break
        self._transition()
    self._persist_session()
    return self._state
```

`runtime_cli.py` 新增三个子命令（parser 注册 + handler）：

```python
# parser 注册（在 explain_p 之后）
pending_p = sub.add_parser("pending", help="show pending human gate")
pending_p.add_argument("--session", required=True)
pending_p.set_defaults(func=_handle_pending)

approve_p = sub.add_parser("approve", help="approve pending patch and resume")
approve_p.add_argument("--session", required=True)
approve_p.set_defaults(func=_handle_approve)

reject_p = sub.add_parser("reject", help="reject and escalate to human")
reject_p.add_argument("--session", required=True)
reject_p.set_defaults(func=_handle_reject)

# handlers
def _handle_pending(args):
    data = json.loads(Path(args.session).read_text(encoding="utf-8"))
    node = data.get("current_node", "?")
    status = data.get("node_status", "?")
    gate = data.get("pending_human_gate", False)
    print(f"node={node} status={status} pending_human_gate={gate}")
    if gate:
        artifacts = data.get("artifacts_dir", "")
        patch_path = Path(artifacts) / "patch_suggestion.json" if artifacts else None
        if patch_path and patch_path.is_file():
            print(f"patch: {patch_path}")
    return 0

def _handle_approve(args):
    p = Path(args.session)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["pending_human_gate"] = False
    data["node_status"] = "APPROVED"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return _handle_resume(args)

def _handle_reject(args):
    p = Path(args.session)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["pending_human_gate"] = False
    data["terminal_state"] = RuntimeTerminalState.ESCALATE_HUMAN.value
    data["transition_reason"] = "human rejected patch"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("terminal_state=ESCALATE_HUMAN")
    return 1
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_human_gate.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): human-in-loop 门（pending/approve/reject CLI + run 退出逻辑）"
```

---

## Phase 6：旧架构删除

### Task 10: 删除 state.py + policy.py

**Files:**
- Delete: `controller/python/loop_controller/state.py`
- Delete: `controller/python/loop_controller/policy.py`
- Delete: `controller/python/tests/test_policy.py`
- Modify: `controller/python/loop_controller/__init__.py`（移除 policy/state re-export）
- Modify: `controller/python/loop_controller/stages.py`（移除 decide_stage 中 policy import，内联逻辑）
- Test: `controller/python/tests/test_legacy_removal.py`（新建）

> **前置研究**：`policy.py` 导出 `decide_termination`，被 `stages.py:239` 的 `decide_stage` 函数引用。删除前需将 decide_termination 逻辑迁移到 stages.py 内联或确认 decide_stage 不再被调用。

- [ ] **Step 1: 写验证测试**

```python
# tests/test_legacy_removal.py
import importlib
import pytest

def test_policy_module_not_importable():
    with pytest.raises(ImportError):
        importlib.import_module("loop_controller.policy")

def test_state_module_not_importable():
    with pytest.raises(ImportError):
        importlib.import_module("loop_controller.state")

def test_stages_importable_without_policy():
    import loop_controller.stages
    assert hasattr(loop_controller.stages, "run_verify_stage")

def test_controller_init_clean():
    import loop_controller
    assert not hasattr(loop_controller, "decide_termination")
    assert not hasattr(loop_controller, "new_session")
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_legacy_removal.py -v`
Expected: FAIL（模块仍可导入）

- [ ] **Step 3: 执行删除 + 修改引用**

1. 读取 `policy.py` 确认 `decide_termination` 逻辑
2. 读取 `stages.py:239` 确认 `decide_stage` 如何调用 `decide_termination`
3. 如果 `decide_stage` 仍被使用：将 `decide_termination` 逻辑内联到 `decide_stage` 中
4. 如果 `decide_stage` 不再被使用（runtime 用 guard_chain 替代）：标记 `decide_stage` 为 deprecated 或一并清理
5. 修改 `__init__.py`：移除 `from loop_controller.policy import decide_termination` 和 `from loop_controller.state import new_session`
6. 删除 `policy.py`、`state.py`、`test_policy.py`

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_legacy_removal.py -v && python -m pytest tests/ -v --ignore=tests/test_policy.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "refactor(loop-controller): 删除 v1 旧架构 state.py + policy.py"
```

---

### Task 11: 删除 control_cli.py

**Files:**
- Delete: `controller/python/loop_controller/control_cli.py`
- Delete: `controller/python/tests/test_control_cli.py`
- Modify: `controller/python/loop_controller/__init__.py`（移除 add_control_parser re-export）
- Modify: `engineering/loop/core/python/loop_core/cli.py:91-96`（移除 control 子命令挂载点）
- Test: `tests/test_legacy_removal.py`（扩充）

- [ ] **Step 1: 写验证测试**

```python
# tests/test_legacy_removal.py 追加
def test_control_cli_not_importable():
    with pytest.raises(ImportError):
        importlib.import_module("loop_controller.control_cli")

def test_loop_core_cli_no_control_subcommand():
    try:
        from loop_controller.control_cli import add_control_parser
        assert False, "add_control_parser should not be importable"
    except ImportError:
        pass
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 执行删除**

1. 修改 `loop_core/cli.py`：删除 L91-96 的 try/except 挂载块：

```python
# 删除这段
try:
    from loop_controller.control_cli import add_control_parser
    add_control_parser(sub)
except ImportError:
    pass
```

2. 修改 `__init__.py`：移除 `from loop_controller.control_cli import add_control_parser`
3. 删除 `control_cli.py`、`test_control_cli.py`

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_legacy_removal.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "refactor(loop-controller): 删除 v1 control_cli.py + loop_core/cli.py 挂载点清理"
```

---

### Task 12: 删除 run_lcview_adb_suite.sh + 文档同步 + 测试更新

**Files:**
- Delete: `engineering/loop/workflows/lcview-adb-run/`（整个目录：README.md / WORKFLOW.md / run_lcview_adb_suite.sh）
- Modify: `engineering/loop/workflows/README.md`（移除 lcview-adb-run 描述）
- Modify: `engineering/loop/WORKFLOW.md`（重写 SOP 为 runtime 驱动，移除 le control 章节）
- Modify: `engineering/loop/controller/README.md`（标注 runtime_cli 唯一入口）
- Modify: `engineering/loop/core/python/tests/test_diagnosis_contract_docs.py:32-35`（更新断言）

> **关键风险**：`test_diagnosis_contract_docs.py:32-35` 有 4 个 assert 断言 WORKFLOW.md 包含 "le control decide/apply-patch/compile/revert"。删除 WORKFLOW.md 段落后必须同步更新该测试，否则会 FAIL。

- [ ] **Step 1: 先读 test_diagnosis_contract_docs.py 确认断言**

```bash
# 读取测试文件，确认 4 个 assert 的确切内容
```

- [ ] **Step 2: 修改测试断言**

将 `test_diagnosis_contract_docs.py:32-35` 中对 "le control decide/apply-patch/compile/revert" 的断言改为验证 "le runtime" 相关内容（如 `le runtime run` / `le runtime init` 等）。

- [ ] **Step 3: 修改文档**

1. `engineering/loop/WORKFLOW.md`：重写 SOP 部分，将 8 步手动 SOP 改为 runtime 自动驱动描述
2. `engineering/loop/controller/README.md`：删除 control_cli 段落，标注 runtime_cli 为唯一入口
3. `engineering/loop/workflows/README.md`：移除 lcview-adb-run 描述
4. `engineering/loop/README.md`：移除 `le control deploy` 引用

- [ ] **Step 4: 删除 workflows 目录**

```bash
rm -rf engineering/loop/workflows/lcview-adb-run/
```

- [ ] **Step 5: 运行全量测试确认通过**

Run: `python -m pytest engineering/ --tb=short -v`
Expected: PASS（含修改后的 test_diagnosis_contract_docs）

- [ ] **Step 6: 提交**

```bash
git add -A && git commit -m "refactor(loop-controller): 删除 v1 workflow 脚本 + 文档同步为 runtime 驱动"
```

---

## Phase 7：工程债清理

### Task 13: stages.py 全局状态消除（StageContext）

**Files:**
- Modify: `stages.py`（新增 `StageContext` dataclass + 函数签名增加 ctx 参数）
- Modify: `engine.py`（调用 stages 函数时传入 ctx）
- Test: `tests/test_stages.py`（扩充）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_stages.py 追加
from loop_controller.stages import StageContext

def test_stage_context_dataclass():
    ctx = StageContext(
        cases_dir="/tmp/cases", device_profile="rp5",
        artifacts_dir="/tmp/artifacts", session_id="s1",
    )
    assert ctx.cases_dir == "/tmp/cases"
    assert ctx.device_profile == "rp5"
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`stages.py` 新增 StageContext：

```python
from dataclasses import dataclass

@dataclass
class StageContext:
    """Per-session stage 执行上下文，消除模块级全局状态。"""
    cases_dir: str = ""
    device_profile: str = ""
    artifacts_dir: str = ""
    session_id: str = ""
```

`run_verify_stage` 签名扩展（向后兼容：优先用 ctx，回退显式参数，再回退全局）：

```python
def run_verify_stage(session_path, suite, output_dir="", *,
                     cases_dir="", device_profile="",
                     ctx: StageContext | None = None) -> dict:
    if ctx:
        cases_dir = cases_dir or ctx.cases_dir
        device_profile = device_profile or ctx.device_profile
    _cases = cases_dir or _CASES_DIR
    _profile = device_profile or _DEVICE_PROFILE
    ...
```

`engine.py` `__init__` 新增：

```python
from loop_controller.stages import StageContext
self._stage_ctx = StageContext(
    cases_dir=cases_dir, device_profile=device_profile,
    artifacts_dir=session.artifacts_dir, session_id=session.session_id,
)
```

`engine.py` 调用 `run_verify_stage` 时传入 `ctx=self._stage_ctx`。

- [ ] **Step 4: 运行确认通过**

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "refactor(loop-controller): stages.py 全局状态消除，引入 StageContext"
```

---

### Task 14: patch_applier 升级（line_range + diff 模式）

**Files:**
- Modify: `analyzer_protocol.py`（`FileChange` 新增 `line_range` / `diff` 字段）
- Modify: `patch_applier.py`（edit 分支支持三种模式）
- Test: `tests/test_patch_applier.py`（扩充）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_patch_applier.py 追加
from loop_controller.patch_applier import apply_file_changes
from loop_controller.analyzer_protocol import FileChange

def test_line_range_edit(tmp_path):
    f = tmp_path / "test.c"
    f.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
    change = FileChange(
        workspace_path="test.c",
        line_range=(2, 3),
        new_content="REPLACED\n",
    )
    result = apply_file_changes([change], str(tmp_path))
    assert result.success
    assert f.read_text() == "line1\nREPLACED\nline4\n"

def test_unified_diff_edit(tmp_path):
    f = tmp_path / "test.c"
    f.write_text("old line\n", encoding="utf-8")
    diff = "--- a/test.c\n+++ b/test.c\n@@ -1 +1 @@\n-old line\n+new line\n"
    change = FileChange(workspace_path="test.c", diff=diff)
    result = apply_file_changes([change], str(tmp_path))
    assert result.success
    assert "new line" in f.read_text()

def test_marker_mode_still_works(tmp_path):
    f = tmp_path / "test.c"
    f.write_text("hello world\n", encoding="utf-8")
    change = FileChange(workspace_path="test.c", old_marker="hello", new_content="goodbye")
    result = apply_file_changes([change], str(tmp_path))
    assert result.success
    assert "goodbye world" in f.read_text()

def test_diff_mode_priority_over_marker(tmp_path):
    """diff 非空时优先用 diff，忽略 old_marker。"""
    f = tmp_path / "test.c"
    f.write_text("a\n", encoding="utf-8")
    diff = "--- a/test.c\n+++ b/test.c\n@@ -1 +1 @@\n-a\n+b\n"
    change = FileChange(workspace_path="test.c", diff=diff, old_marker="should_be_ignored")
    result = apply_file_changes([change], str(tmp_path))
    assert result.success
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

`analyzer_protocol.py` `FileChange` 新增字段：

```python
@dataclass
class FileChange:
    workspace_path: str
    change_type: Literal["edit", "create", "delete"] = "edit"
    old_marker: str = ""
    new_content: str = ""
    line_range: tuple[int, int] | None = None
    diff: str = ""
```

`patch_applier.py` `apply_file_changes` edit 分支改造（在现有 `if change.change_type == "edit":` 内部）：

```python
if change.change_type == "edit":
    fp = Path(workspace_root) / change.workspace_path
    if not fp.exists():
        return ApplyResult(success=False, error=f"file not found: {fp}")
    content = fp.read_text(encoding="utf-8")
    # 优先级 1: unified diff
    if change.diff:
        import subprocess
        diff_file = fp.parent / f".{fp.name}.patch"
        diff_file.write_text(change.diff, encoding="utf-8")
        r = subprocess.run(
            ["git", "apply", "--recount", str(diff_file)],
            cwd=workspace_root, capture_output=True, text=True,
        )
        diff_file.unlink(missing_ok=True)
        if r.returncode != 0:
            return ApplyResult(success=False,
                               error=f"git apply failed: {r.stderr[:200]}")
        applied.append(change.workspace_path)
        continue
    # 优先级 2: line_range
    if change.line_range:
        lines = content.splitlines(keepends=True)
        start, end = change.line_range
        new_lines = lines[:start-1] + [change.new_content] + lines[end:]
        fp.write_text("".join(new_lines), encoding="utf-8")
        applied.append(change.workspace_path)
        continue
    # 优先级 3: old_marker
    count = content.count(change.old_marker)
    if count != 1:
        return ApplyResult(success=False,
                           error=f"old_marker found {count} times, not unique")
    content = content.replace(change.old_marker, change.new_content, 1)
    fp.write_text(content, encoding="utf-8")
    applied.append(change.workspace_path)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_patch_applier.py -v`
Expected: PASS（含新增 + 原有）

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "feat(loop-controller): patch_applier 支持 line_range + unified diff 模式"
```

---

### Task 15: 文档同步 + 全量回归

**Files:**
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/controller/README.md`
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/loop/contracts/README.md`（failure_codes 已扩展，同步文档）

- [ ] **Step 1: 更新所有文档**

1. `engineering/loop/README.md`：更新目录结构（删除旧文件，新增 analyzer.yaml / patch_knowledge_base.json）
2. `engineering/loop/controller/README.md`：更新 guard 清单（如有新增）、analyzer 架构说明、删除 control_cli 段落
3. `engineering/loop/WORKFLOW.md`：确保 SOP 已在 T12 中重写
4. `engineering/loop/contracts/README.md`：同步 failure_codes 到 16 个

- [ ] **Step 2: 全量回归测试**

Run:
```bash
PYTHONPATH=engineering/loop/controller/python:engineering/loop/contracts/python:\
engineering/loop/core/python:engineering/loop/deploy/python:\
engineering/loop/connection/rp5-serial/python:\
engineering/loop/connection/adb/python \
python3 -m pytest engineering/ --tb=short -v
```

Expected:
- 基线 178 passed 中移除 test_control_cli / test_policy（约 -N 个）
- 新增 test_knowledge_base / test_opencode_analyzer / test_chained_analyzer / test_human_gate / test_legacy_removal
- **零回归**

- [ ] **Step 3: 提交**

```bash
git add -A && git commit -m "docs(loop-controller): 文档同步 + 全量回归验证通过"
```

---

## 自检清单

### Spec 覆盖

| Spec 要求 | 对应 Task |
|-----------|----------|
| P0-1 三层降级 Analyzer | T1-T4 + T5（CLI 注入） |
| P0-2 规则库扩充（lciod 3 bug） | T7 |
| P0-3 知识积累（Reflexion） | T6 |
| P1-1 删除旧架构 | T10（state/policy）+ T11（control_cli）+ T12（workflow + 文档） |
| P1-2 stages.py 全局状态消除 | T13 |
| P1-3 confidence 阈值检查 | T8 |
| P1-4 human-in-loop 门 | T9 |
| P1-5 补丁格式升级 | T14 |
| 文档同步 + 全量回归 | T15 |

### 风险点

| 风险 | 缓解 |
|------|------|
| T7 的 old_marker 需对照实际源码 | Step 0 明确要求先读取源码确认 |
| T10 删除 policy.py 可能 break stages.py | Step 3 先迁移 decide_termination 逻辑 |
| T12 删 WORKFLOW.md 段落 break test_diagnosis_contract_docs | Step 1-2 先改测试再删段落 |
| OpencodeAnalyzer subprocess 依赖 opencode CLI | 已有 mock 测试 + timeout 降级 |
| PyYAML 可能未安装 | _load_analyzer_config 有 try/except 回退 |

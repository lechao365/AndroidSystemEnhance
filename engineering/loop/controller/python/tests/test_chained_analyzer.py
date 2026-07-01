"""ChainedAnalyzer 单元测试：三层降级编排（KB→规则→opencode）。"""
import json

from loop_controller.analyzer_protocol import (
    AnalysisRequest,
    ChainedAnalyzer,
    FileChange,
    LlmAnalyzer,
    OpencodeAnalyzer,
    PatchSuggestion,
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
    layer1 = _StubAnalyzer(patches=p1, name="hit")
    layer2 = _StubAnalyzer(patches=[], name="empty")
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


def test_patch_suggestion_has_matched_layer_field():
    """G9: PatchSuggestion 必须有 matched_layer 字段，默认空串。"""
    from dataclasses import fields
    names = {f.name for f in fields(PatchSuggestion)}
    assert "matched_layer" in names, "PatchSuggestion 缺少 matched_layer 字段"
    ps = PatchSuggestion()
    assert ps.matched_layer == ""


def test_chained_fills_matched_layer_kb():
    """G9: KB 层命中时 matched_layer 填类名。"""
    p = [FileChange(workspace_path="a.c")]
    chain = ChainedAnalyzer([_StubAnalyzer(patches=p, name="KnowledgeBaseAnalyzer")])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert result.matched_layer == "_StubAnalyzer"


def test_chained_fills_matched_layer_second_layer():
    """G9: 第二层命中时 matched_layer 填第二层类名。"""
    p = [FileChange(workspace_path="a.c")]
    chain = ChainedAnalyzer([
        _StubAnalyzer(patches=[], name="empty"),
        _StubAnalyzer(patches=p, name="hit"),
    ])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert result.matched_layer == "_StubAnalyzer"


def test_chained_no_match_leaves_matched_layer_empty():
    """G9: 三层均空时 matched_layer 保持空串。"""
    chain = ChainedAnalyzer([_StubAnalyzer(patches=[])])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert result.matched_layer == ""


def test_patch_suggestion_has_candidate_fields() -> None:
    """G2: PatchSuggestion 必须有 candidate_id 和 candidate_index 字段。"""
    from loop_controller.analyzer_protocol import PatchSuggestion, FileChange

    sug = PatchSuggestion(
        target_files=[FileChange(workspace_path="a.c")],
        candidate_id="c0",
        candidate_index=0,
    )
    assert sug.candidate_id == "c0"
    assert sug.candidate_index == 0
    sug2 = PatchSuggestion()
    assert sug2.candidate_id == ""
    assert sug2.candidate_index == 0


def test_llm_analyzer_analyze_n_default() -> None:
    """G2: LlmAnalyzer.analyze_n 默认实现循环调 analyze。"""
    from loop_controller.analyzer_protocol import LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange

    class FixedAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")])

    a = FixedAnalyzer()
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = a.analyze_n(req, 3)
    assert len(results) == 3
    assert all(r.target_files for r in results)


def test_llm_analyzer_analyze_n_empty() -> None:
    """G2: analyze_n 遇到空产出不收集。"""
    from loop_controller.analyzer_protocol import LlmAnalyzer, AnalysisRequest, PatchSuggestion

    class EmptyAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[])

    a = EmptyAnalyzer()
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = a.analyze_n(req, 3)
    assert len(results) == 0


def test_chained_analyzer_analyze_n_collects_all_layers() -> None:
    """G2: ChainedAnalyzer.analyze_n 收集所有层非空产出，不短路。"""
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer, LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange,
    )

    class LayerA(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")], confidence=0.9)

    class LayerB(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="b.c")], confidence=0.8)

    chained = ChainedAnalyzer([LayerA(), LayerB()])
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = chained.analyze_n(req, 3)
    assert len(results) == 2
    assert results[0].matched_layer == "LayerA"
    assert results[1].matched_layer == "LayerB"
    assert "[LayerA]" in results[0].rationale


def test_chained_analyzer_analyze_n_caps_at_n() -> None:
    """G2: ChainedAnalyzer.analyze_n 不超过 N 个候选。"""
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer, LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange,
    )

    class LayerA(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")])

    class LayerB(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="b.c")])

    chained = ChainedAnalyzer([LayerA(), LayerB()])
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = chained.analyze_n(req, 1)
    assert len(results) == 1


def test_chained_analyzer_analyze_n_skips_empty_layers() -> None:
    """G2: 空产出的层被跳过，不影响其他层。"""
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer, LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange,
    )

    class EmptyLayer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[])

    class GoodLayer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")])

    chained = ChainedAnalyzer([EmptyLayer(), GoodLayer()])
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = chained.analyze_n(req, 3)
    assert len(results) == 1
    assert results[0].matched_layer == "GoodLayer"


def test_opencode_analyzer_analyze_n_returns_multiple(monkeypatch) -> None:
    """G2: OpencodeAnalyzer.analyze_n(n>1) 返回多个候选。"""
    from loop_controller.analyzer_protocol import OpencodeAnalyzer, AnalysisRequest

    call_count = {"n": 0}

    def fake_invoke(self, prompt, req_file):
        call_count["n"] += 1
        idx = call_count["n"] - 1
        text = f'[{{"workspace_path": "a{idx}.c", "change_type": "edit", "new_content": "// fix {idx}"}}]'
        events = [{"type": "text", "part": {"text": text}}]
        return "\n".join(json.dumps(e) for e in events)

    monkeypatch.setattr(OpencodeAnalyzer, "_invoke_opencode", fake_invoke)

    a = OpencodeAnalyzer(workspace_root="/tmp/ws")
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = a.analyze_n(req, 2)
    assert len(results) == 2
    assert results[0].candidate_index == 0
    assert results[1].candidate_index == 1


def test_opencode_analyzer_analyze_n_dedup_by_hash(monkeypatch) -> None:
    """G2: 相同 patch_hash 的候选去重。"""
    from loop_controller.analyzer_protocol import OpencodeAnalyzer, AnalysisRequest

    def fake_invoke(self, prompt, req_file):
        text = '[{"workspace_path": "a.c", "change_type": "edit", "new_content": "// fix"}]'
        events = [{"type": "text", "part": {"text": text}}]
        return "\n".join(json.dumps(e) for e in events)

    monkeypatch.setattr(OpencodeAnalyzer, "_invoke_opencode", fake_invoke)

    a = OpencodeAnalyzer(workspace_root="/tmp/ws")
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = a.analyze_n(req, 3)
    assert len(results) == 1

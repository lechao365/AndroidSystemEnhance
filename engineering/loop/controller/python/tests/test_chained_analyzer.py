"""ChainedAnalyzer 单元测试：三层降级编排（KB→规则→opencode）。"""
from loop_controller.analyzer_protocol import (
    AnalysisRequest,
    ChainedAnalyzer,
    FileChange,
    LlmAnalyzer,
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

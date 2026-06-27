"""LlmAnalyzer 抽象接口 + AnalysisRequest / PatchSuggestion 数据模型。

默认实现是主会话本身（不走代码），此接口供未来接 API/子进程扩展。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class AnalysisRequest:
    session_id: str
    attempt_index: int
    failed_cases: list[dict] = field(default_factory=list)
    evidence_bundle_path: str = ""
    collectors_output: dict = field(default_factory=dict)
    workspace_diff_so_far: str = ""
    hints: str = ""


@dataclass
class FileChange:
    workspace_path: str
    change_type: Literal["edit", "create", "delete"] = "edit"
    old_marker: str = ""
    new_content: str = ""


@dataclass
class PatchSuggestion:
    target_files: list[FileChange] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.0
    deploy_mode_hint: str = ""


class LlmAnalyzer(ABC):
    @abstractmethod
    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        ...


class ScriptedAnalyzer(LlmAnalyzer):
    """基于确定性规则的占位分析器。

    本期为规则库留空实现：无论输入如何，永远返回空补丁与零置信度，
    表明无确定性规则可应用，需人工/AI 介入。规则库后续迭代补齐。
    """

    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        return PatchSuggestion(
            target_files=[],
            rationale="无确定性规则可应用，需人工/AI 介入",
            confidence=0.0,
        )

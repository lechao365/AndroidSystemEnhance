"""LlmAnalyzer 抽象接口 + AnalysisRequest / PatchSuggestion 数据模型。

默认实现是主会话本身（不走代码），此接口供未来接 API/子进程扩展。
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
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


_FV_MAIN_C_PATH = "patchs/rpi5/others/usb-verify/src/cli/main.c"


def _rule_fv_stdout_pollution(case: dict) -> list[FileChange] | None:
    """FV_STDOUT_POLLUTION：fault-verify stdout 污染 JSON 输出。

    触发条件：failure_reason 含 "output is not valid JSON" 且 command 含
    "stats reset" 或 "config set"（说明链式命令中前置子命令的 printf 污染了 --json 输出）。
    修复：将 main.c 中 printf("State reset OK") / printf("Config set OK")
    改为 fprintf(stderr, ...)，让进度信息走 stderr 不污染 stdout JSON。
    """
    reason = (case.get("failure_reason") or "").lower()
    command = (case.get("command") or "").lower()
    if "output is not valid json" not in reason:
        return None
    has_reset = "stats reset" in command or "state reset" in command
    has_config = "config set" in command
    if not (has_reset or has_config):
        return None
    changes: list[FileChange] = []
    if has_reset:
        changes.append(FileChange(
            workspace_path=_FV_MAIN_C_PATH,
            change_type="edit",
            old_marker='        printf("State reset OK\\n");',
            new_content='        fprintf(stderr, "State reset OK\\n");',
        ))
    if has_config:
        changes.append(FileChange(
            workspace_path=_FV_MAIN_C_PATH,
            change_type="edit",
            old_marker='        printf("Config set OK\\n");',
            new_content='        fprintf(stderr, "Config set OK\\n");',
        ))
    return changes if changes else None


_RULES = [
    _rule_fv_stdout_pollution,
]


class ScriptedAnalyzer(LlmAnalyzer):
    """基于确定性规则的分析器。

    匹配失败指纹 → 产出确定性补丁；无匹配 → 返回空补丁退人工。
    规则按优先级顺序求值，首个匹配即返回（多规则命中取并集）。
    """

    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        cases_index = self._load_cases_index(request)
        all_changes: list[FileChange] = []
        matched_rules: list[str] = []
        for fc in request.failed_cases:
            full_case = self._enrich_case(fc, cases_index)
            for rule in _RULES:
                result = rule(full_case)
                if result:
                    all_changes.extend(result)
                    matched_rules.append(rule.__name__)
        if all_changes:
            return PatchSuggestion(
                target_files=all_changes,
                rationale=f"确定性规则匹配：{', '.join(set(matched_rules))}",
                confidence=0.95,
                deploy_mode_hint="PUSH_SINGLE",
            )
        return PatchSuggestion(
            target_files=[],
            rationale="无确定性规则可应用，需人工/AI 介入",
            confidence=0.0,
        )

    def _load_cases_index(self, request: AnalysisRequest) -> dict[str, dict]:
        """从 evidence_bundle.json 加载完整 case 信息（含 assertion/output）。"""
        if not request.evidence_bundle_path or not os.path.isfile(request.evidence_bundle_path):
            return {}
        try:
            data = json.loads(Path(request.evidence_bundle_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return {c.get("id", ""): c for c in data.get("cases", []) if isinstance(c, dict)}

    def _enrich_case(self, failed_case: dict, cases_index: dict[str, dict]) -> dict:
        """用 evidence_bundle 的完整信息补全 failed_case（缺字段才补）。"""
        case_id = failed_case.get("id", "")
        full = cases_index.get(case_id, {})
        enriched = dict(failed_case)
        for key in ("assertion", "output", "output_preview", "command", "failure_reason"):
            if key not in enriched and key in full:
                enriched[key] = full[key]
        return enriched

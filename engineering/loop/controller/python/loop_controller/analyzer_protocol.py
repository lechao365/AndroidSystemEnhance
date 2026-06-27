"""LlmAnalyzer 抽象接口 + AnalysisRequest / PatchSuggestion 数据模型。

默认实现是主会话本身（不走代码），此接口供未来接 API/子进程扩展。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
    target: str = ""
    suite: str = ""


@dataclass
class FileChange:
    workspace_path: str
    change_type: Literal["edit", "create", "delete"] = "edit"
    old_marker: str = ""
    new_content: str = ""
    line_range: tuple[int, int] | None = None
    diff: str = ""


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
_LCIOD_HAL_PATH = "vendor/lechao/services/lechao_lciod/service.cpp"
_LCIOD_DAEMON_PATH = "vendor/lechao/services/lechao_lciod/daemon.cpp"


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


def _rule_lciod_hal_field_inversion(case: dict) -> list[FileChange] | None:
    """LCIOD HAL getStats 字段反转：read_bytes 和 write_bytes 值互换。

    触发条件：failure_reason 同时提到 read_bytes / write_bytes，且含
    "mismatch" 或 "expected"（断言失败说明两个字段被对调赋值）。
    修复：在 HAL service.cpp 中把 read_bytes / write_bytes 的赋值交换回来。
    """
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
    """LCIOD Daemon getAverageRate 公式错误（速率出现负值或 NaN）。

    触发条件：failure_reason 含 "getaveragerate" 且断言失败。
    修复：把速率公式从 bytes / (interval_ns / 1e9) 修正为 bytes * 1e9 / interval_ns，
    避免整数除法或量纲错误导致负值/除零。
    """
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
    """LCIOD HAL readEvent 排空遗漏（事件丢失）。

    触发条件：failure_reason 含 "readevent" 且事件数异常
    （"0 events" 或 "incomplete"），说明只读了一次未排空内核缓冲。
    修复：在 service.cpp readEvent 返回前增加排空循环。
    """
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
    """计算失败指纹（sha256 前缀），保证同义失败归一为同一指纹。

    归一化规则：
    - 失败用例按 id 排序（顺序无关）。
    - failure_reason 截断到 reason_length 字符。
    - 空白字符合并、首尾去空、转小写。
    - 文件路径 (/a/b.c) 替换为 <path>，消除环境差异。
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


class KnowledgeBaseAnalyzer(LlmAnalyzer):
    """从 patch_knowledge_base.json 加载历史成功补丁，按 fingerprint 匹配。

    命中：返回 KB 中对应补丁，confidence=hit_confidence（默认 0.98）。
    未命中/加载失败：返回空补丁，confidence=0.0。
    """

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


class OpencodeAnalyzer(LlmAnalyzer):
    """通过 subprocess 调 opencode run，让 LLM 生成补丁。

    设计要点：
    - prompt 由失败用例 + evidence 路径 + 历史 diff 拼装。
    - 输出要求严格 JSON 数组（FileChange 列表）。
    - 任意异常（超时、非零退出、解析失败）都降级为空补丁，不阻断链式降级。
    """

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
            return PatchSuggestion(
                target_files=[], confidence=0.0,
                rationale="opencode analyzer 失败",
            )

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
        import tempfile
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
        import subprocess
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


class ChainedAnalyzer(LlmAnalyzer):
    """三层降级：KB → 规则 → opencode。首个非空产出即返回。

    - 顺序求值 layers，命中（target_files 非空）即返回。
    - 某层抛异常则跳过，继续下一层（容错）。
    - 全部为空时返回空补丁，rationale 标注"三层 analyzer 均无产出"。
    - 命中层的 rationale 前缀追加该层类名，便于审计来源。
    """

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

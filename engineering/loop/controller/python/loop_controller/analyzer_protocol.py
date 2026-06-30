"""LlmAnalyzer 抽象接口 + AnalysisRequest / PatchSuggestion 数据模型。

默认实现是主会话本身（不走代码），此接口供未来接 API/子进程扩展。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time as _time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
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
    prior_attempts: list[dict] = field(default_factory=list)


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
    matched_layer: str = ""


class LlmAnalyzer(ABC):
    @abstractmethod
    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        ...


_FV_MAIN_C_PATH = "patchs/rpi5/others/usb-verify/src/cli/main.c"
_LCIOD_HAL_PATH = "vendor/lechao/services/lechao_lciod/service.cpp"
_LCIOD_DAEMON_PATH = "vendor/lechao/services/lechao_lciod/daemon.cpp"
_LCVIEW_HAL_PATH = "vendor/lechao/services/lechao_lcview/hal/LcView.cpp"
_LCVIEW_DAEMON_PATH = "vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.cpp"


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


def _rule_lcview_hal_connect_fault(case: dict) -> list[FileChange] | None:
    """LCVIEW validate / HAL connect 故障：daemon logcat 含故障日志。

    两种触发路径：
    1. **直接文本匹配**：failure_reason 含 "connect failed" / "cannot cast to ILcView"，
       且涉及 lcview_hal。
    2. **case_id 匹配**：case_id == "lcview_no_validate_errors" 且 output 非 0
       （verify 用例的 command 是 grep|wc -l，failure_reason 只有计数）。

    修复动作：删除注入的故障日志行（daemon main 入口的 FAULT-INJECTED 行）。
    confidence: 0.95（确定性规则）
    """
    reason = (case.get("failure_reason") or "").lower()
    command = (case.get("command") or "").lower()
    case_id = (case.get("id") or "").lower()

    # 路径 2：case_id 匹配（verify 用例计数非 0 → 有故障日志）
    if case_id == "lcview_no_validate_errors":
        output = (case.get("output") or "").strip()
        if output and output != "0":
            return _lcview_fault_patch()

    # 路径 1：直接文本匹配
    if "lechao_lcview_hal" not in command and "lcview" not in reason:
        return None
    if "connect failed" not in reason and "cannot cast to ilcview" not in reason:
        return None
    return _lcview_fault_patch()


def _lcview_fault_patch() -> list[FileChange]:
    """构造 lcview daemon 故障日志删除补丁。"""
    return [FileChange(
        workspace_path=_LCVIEW_DAEMON_PATH,
        change_type="edit",
        old_marker='    // FAULT-INJECTED: HAL connect 故障\n    ALOGE("lechao_lcview: connect failed: cannot cast to ILcView (fault injected)");\n',
        new_content='',
    )]


def _rule_lcview_parse_loop_break(case: dict) -> list[FileChange] | None:
    """LCVIEW 解析循环异常中断故障（read-loop fault N1）。

    两种触发路径：
    1. **case_id 匹配**：case_id == "lcview_no_readloop_abort" 且 output 非 0
       （专属 verify 用例 grep 'parse loop aborted'，failure_reason 只有计数）。
    2. **文本匹配**：failure_reason 含 "parse loop aborted" / "read-loop fault n1"。

    修复动作：删除注入的故障日志 + break 行（daemon 解析循环入口的 FAULT-INJECTED-N1 行）。
    confidence: 0.95（确定性规则）
    """
    reason = (case.get("failure_reason") or "").lower()
    case_id = (case.get("id") or "").lower()

    # 路径 1：case_id 匹配（专属 verify 用例计数非 0 → 有故障中断日志）
    if case_id == "lcview_no_readloop_abort":
        output = (case.get("output") or "").strip()
        if output and output != "0":
            return _lcview_parse_loop_patch()

    # 路径 2：直接文本匹配
    if "parse loop aborted" in reason or "read-loop fault n1" in reason:
        return _lcview_parse_loop_patch()
    return None


def _lcview_parse_loop_patch() -> list[FileChange]:
    """构造 lcview daemon 解析循环 break 故障删除补丁。"""
    return [FileChange(
        workspace_path=_LCVIEW_DAEMON_PATH,
        change_type="edit",
        old_marker='    ALOGE("lechao_lcview: parse loop aborted: read-loop fault N1");  // FAULT-INJECTED-N1\n',
        new_content='',
    )]


def _rule_lcview_rc_fault_prop(case: dict) -> list[FileChange] | None:
    """LCVIEW init.rc 注入的故障属性（N6 DD_BOOT_REBOOT 链路验证）。

    两种触发路径：
    1. **case_id 匹配**：case_id == "lcview_no_n6_fault_prop" 且 output 非空
       （专属 verify 用例 getprop lechao.fault.n6，boot 后非空表示 .rc 故障已生效）。
    2. **文本匹配**：failure_reason 含 "lechao.fault.n6"。

    修复动作：删除 daemon/lechao_lcview.rc 中注入的 setprop 故障行。
    confidence: 0.95（确定性规则）；.rc 改动经 decider 自动走 DD_BOOT_REBOOT。
    """
    reason = (case.get("failure_reason") or "").lower()
    case_id = (case.get("id") or "").lower()

    if case_id == "lcview_no_n6_fault_prop":
        output = (case.get("output") or "").strip()
        if output:
            return _lcview_rc_fault_patch()

    if "lechao.fault.n6" in reason:
        return _lcview_rc_fault_patch()
    return None


def _lcview_rc_fault_patch() -> list[FileChange]:
    """构造 lcview daemon init.rc 故障属性删除补丁（DD_BOOT_REBOOT）。"""
    return [FileChange(
        workspace_path="vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.rc",
        change_type="edit",
        old_marker="    setprop lechao.fault.n6 injected\n",
        new_content="",
    )]


_RULES = [
    _rule_fv_stdout_pollution,
    _rule_lciod_hal_field_inversion,
    _rule_lciod_daemon_formula_error,
    _rule_lciod_hal_readdrain_missing,
    _rule_lcview_hal_connect_fault,
    _rule_lcview_parse_loop_break,
    _rule_lcview_rc_fault_prop,
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


def save_kb(kb_path: str, entries: list[KBEntry], max_entries: int = 100) -> None:
    """保存知识库到 kb_path，条目数超限时按 hit_count 降序淘汰低命中条目。"""
    if len(entries) > max_entries:
        entries.sort(key=lambda e: e.hit_count, reverse=True)
        entries = entries[:max_entries]
    data = {"version": 1, "entries": [asdict(e) for e in entries]}
    parent = os.path.dirname(kb_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    Path(kb_path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_kb(kb_path: str, fingerprint: str, fingerprint_components: dict,
              patch: list[dict], description: str, deploy_mode_hint: str,
              source_session: str, source_attempt: int, max_entries: int = 100) -> None:
    """更新知识库：同 fingerprint 则覆盖更新，否则追加新条目。"""
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


def _normalize_reason(reason: str) -> str:
    """归一化 failure_reason：消除动态数值差异，保留语义骨架。

    规则（按顺序应用）：
    1. 文件路径 (/a/b.c) → <path>
    2. 十六进制地址 (0x7fff100) → <hex>
    3. 整数计数 (count=12345 / got: 2) → <num>
    4. 空白合并、首尾去空、转小写
    """
    reason = re.sub(r"/[\w/.-]+", "<path>", reason)
    reason = re.sub(r"\b0x[0-9a-fA-F]+\b", "<hex>", reason)
    reason = re.sub(r"(?<![<])\b\d+\b", "<num>", reason)
    reason = re.sub(r"\s+", " ", reason).strip().lower()
    return reason


def _compute_fingerprint(request: AnalysisRequest, reason_length: int = 80) -> str:
    """计算失败指纹（sha256 前缀），保证同义失败归一为同一指纹。

    归一化规则：
    - 失败用例按 id 排序（顺序无关）。
    - failure_reason 截断到 reason_length 字符。
    - 动态数值归一化：文件路径→<path>、十六进制地址→<hex>、整数→<num>。
    - 空白字符合并、首尾去空、转小写。
    """
    components = []
    for fc in sorted(request.failed_cases, key=lambda c: c.get("id", "")):
        case_id = fc.get("id", "")
        reason = (fc.get("failure_reason") or "")[:reason_length]
        reason = _normalize_reason(reason)
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
                # 命中：更新 hit_count / last_hit_at 并写回 KB
                entry.hit_count += 1
                entry.last_hit_at = _time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                try:
                    save_kb(self._kb_path, self._kb)
                except Exception:
                    pass  # KB 写回失败不影响命中返回
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
            lines.append("- 请勿重复上述已失败的修复方向。")
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
        cmd.extend(["-f", req_file, "--", prompt])
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=self._timeout, cwd=self._workspace_root,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"opencode exited {result.returncode}: {result.stderr[:200]}")
        return result.stdout

    def _parse_suggestion(self, output: str) -> PatchSuggestion:
        """从 opencode run --format json 的 JSONL 流式输出中提取补丁。

        opencode --format json 实际输出为 JSONL（每行一个事件），格式：
            {"type":"text","part":{"text":"<LLM 文本片段>"}}
        多个 text 事件需按顺序拼接为完整 LLM 文本，再从中提取 JSON 数组。

        支持的 LLM 文本格式（健壮性兜底）：
        - 裸 JSON 数组：[{"workspace_path": "..."}]
        - markdown 围栏：```json\\n[...]\\n```
        - 含解释文字：提取首个平衡的 [...] 块
        """
        full_text = self._extract_assistant_text(output)
        if not full_text:
            return PatchSuggestion(target_files=[], confidence=0.0)
        patches_json = self._extract_json_array(full_text)
        if patches_json is None:
            return PatchSuggestion(target_files=[], confidence=0.0)
        try:
            patches = json.loads(patches_json)
            if isinstance(patches, list) and patches:
                changes = [FileChange(**p) for p in patches]
                return PatchSuggestion(
                    target_files=changes, confidence=0.8,
                    rationale="opencode LLM 生成",
                )
        except (json.JSONDecodeError, TypeError):
            pass
        return PatchSuggestion(target_files=[], confidence=0.0)

    @staticmethod
    def _extract_assistant_text(output: str) -> str:
        """从 JSONL 流式输出中提取所有 text 事件的 part.text 并拼接。

        opencode --format json 输出每行一个 JSON 事件对象：
            {"type":"text","part":{"text":"..."}}
        兼容旧格式（type=assistant, content=...）。
        """
        parts: list[str] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            ev_type = ev.get("type", "")
            if ev_type == "text":
                text = ev.get("part", {}).get("text", "") if isinstance(ev.get("part"), dict) else ""
                if text:
                    parts.append(text)
            elif ev_type == "assistant":
                content = ev.get("content", "")
                if content:
                    parts.append(content)
        return "".join(parts)

    @staticmethod
    def _extract_json_array(text: str) -> str | None:
        """从 LLM 文本中提取首个 JSON 数组字符串。

        依次尝试：
        1. 去除 markdown 围栏（```json ... ``` 或 ``` ... ```）
        2. 正则匹配首个平衡的 [...] 块
        3. 直接当作裸 JSON
        返回 JSON 数组的原始字符串，或 None。
        """
        import re
        fence_match = re.search(r"```(?:json)?\s*\n(\[.*?\])\s*\n```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1)
        bare_match = re.search(r"(\[[\s\S]*\])", text)
        if bare_match:
            return bare_match.group(1)
        return None


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
                suggestion.matched_layer = type(layer).__name__
                suggestion.rationale = f"[{type(layer).__name__}] {suggestion.rationale}"
                return suggestion
        return PatchSuggestion(
            target_files=[], confidence=0.0,
            rationale="三层 analyzer 均无产出",
        )

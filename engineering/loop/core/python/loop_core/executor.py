"""用例执行器。

职责：执行用例 → 求值断言 → 触发 collector → 组装 EvidenceBundle。
执行顺序遵循 case_loader 的拓扑排序，处理 requires 依赖短路。
"""
from __future__ import annotations

import time
from datetime import datetime
from uuid import uuid4

from loop_core.assertion_engine import AssertionContext, AssertionEngine
from loop_core.case_loader import CaseSuite, TestCase
from loop_core.collector import Collector
from loop_core.models import CollectorResult, EvidenceBundle, TestCaseResult


class CaseExecutor:
    """用例执行器。

    消费 CaseSuite，逐条执行用例，对输出求值断言，
    fail 时触发 on_fail.collectors（同 suite 内去重）。

    Attributes:
        transport: 实现 BaseTransport 接口的实例
        engine: 断言引擎实例
    """

    def __init__(self, transport, engine: AssertionEngine) -> None:
        self.transport = transport
        self.engine = engine

    def execute_suite(
        self,
        suite: CaseSuite,
        device_id: str = "",
        prompt_markers: list[str] | None = None,
        capture_timeout: float = 5.0,
        recent_limit: int = 400,
        boot_markers: list[str] | None = None,
        panic_markers: list[str] | None = None,
    ) -> EvidenceBundle:
        """执行完整用例集。

        Args:
            suite: 加载后的 CaseSuite
            device_id: 设备标识
            prompt_markers: prompt 标记列表（用于 prompt_visible 断言）
            capture_timeout: 用例命令的采集超时
            recent_limit: 采集行数上限
            boot_markers: reboot 检测的两级 boot 标记（action case 使用）
            panic_markers: kernel panic 标记列表（action case 使用）

        Returns:
            EvidenceBundle
        """
        prompt_markers = prompt_markers or []
        results: dict[str, TestCaseResult] = {}
        triggered_collectors: set[str] = set()
        warnings: list[str] = []

        for case in suite.cases:
            result = self._execute_case(
                case, results, prompt_markers, capture_timeout, recent_limit,
                boot_markers=boot_markers, panic_markers=panic_markers,
            )
            results[case.fqn] = result
            # 收集需要执行的 collector（critical fail 才触发）
            if result.status == "fail" and case.severity == "critical":
                for cname in case.on_fail.get("collectors", []):
                    triggered_collectors.add(cname)

        # 执行 collector（去重）
        evidence: dict[str, CollectorResult] = {}
        if triggered_collectors:
            collector_runner = Collector(self.transport)
            for cname in triggered_collectors:
                if cname in suite.collectors:
                    try:
                        evidence[cname] = collector_runner.run(
                            cname,
                            suite.collectors[cname],
                            capture_timeout=capture_timeout,
                            recent_limit=recent_limit,
                            prompt_markers=prompt_markers,
                        )
                    except OSError as exc:
                        # collector 执行失败：降级为空证据并记录告警，不阻断 suite
                        warnings.append(f"collector '{cname}' failed: {exc}")
                        spec = suite.collectors[cname]
                        evidence[cname] = CollectorResult(
                            name=cname,
                            commands=spec.get("commands", []),
                            outputs=[],
                            hints=spec.get("hints", ""),
                        )

        # 统计
        case_list = list(results.values())
        passed = sum(1 for r in case_list if r.status == "pass")
        failed = sum(1 for r in case_list if r.status == "fail")
        skipped = sum(1 for r in case_list if r.status == "skipped")
        errors = sum(1 for r in case_list if r.status == "error")
        # overall 收紧：critical 用例 fail/skipped/error 均判定为 FAIL
        critical_incomplete = sum(
            1
            for result, case in zip(case_list, suite.cases)
            if case.severity == "critical" and result.status in {"fail", "skipped", "error"}
        ) if case_list else 0
        overall = "PASS" if critical_incomplete == 0 else "FAIL"

        summary: dict = {
            "total": len(case_list),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "errors": errors,
            "overall": overall,
        }
        if warnings:
            summary["warnings"] = warnings

        return EvidenceBundle(
            bundle_id=f"eb-{uuid4().hex[:8]}",
            device_id=device_id,
            suite=suite.name,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            summary=summary,
            cases=case_list,
            evidence=evidence,
        )

    def _execute_case(
        self,
        case: TestCase,
        results: dict[str, TestCaseResult],
        prompt_markers: list[str],
        capture_timeout: float,
        recent_limit: int,
        boot_markers: list[str] | None = None,
        panic_markers: list[str] | None = None,
    ) -> TestCaseResult:
        """执行单个用例（含依赖检查）。"""
        # 检查依赖
        for dep_id in case.requires:
            dep_result = results.get(dep_id)
            if dep_result is None:
                # 依赖的用例不存在 → skip
                return TestCaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="skipped",
                    skip_reason=f"dependency '{dep_id}' not found",
                    tags=case.tags,
                )
            if dep_result.status in ("fail", "skipped"):
                dep_status = dep_result.status
                return TestCaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="skipped",
                    skip_reason=f"dependency '{dep_id}' {dep_status}",
                    tags=case.tags,
                )

        # action case 分支（如 action: reboot）
        if case.action == "reboot":
            reboot_fn = getattr(self.transport, "reboot_and_wait", None)
            if reboot_fn is None or not callable(reboot_fn):
                return TestCaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="error",
                    command=case.command,
                    failure_reason="transport does not support reboot_and_wait",
                    error_type="unsupported_action",
                    tags=case.tags,
                )
            start = time.monotonic()
            try:
                reboot_result = reboot_fn(
                    boot_markers=boot_markers or [],
                    panic_markers=panic_markers or [],
                    prompt_markers=prompt_markers,
                )
            except Exception as exc:
                return TestCaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="error",
                    command=case.command,
                    failure_reason=str(exc),
                    error_type=type(exc).__name__,
                    tags=case.tags,
                )
            duration = round(time.monotonic() - start, 3)
            output_text = "\n".join(reboot_result.transcript_lines)
            preview = " | ".join(reboot_result.transcript_lines[:5]) if reboot_result.transcript_lines else ""
            return TestCaseResult(
                id=case.id,
                suite=case.suite,
                status="pass" if reboot_result.status == "pass" else "fail",
                command=case.command,
                output=output_text,
                output_preview=preview,
                assertion={"type": "action", "action": case.action},
                duration_sec=duration,
                failure_reason=reboot_result.failure_reason,
                triggered_collectors=case.on_fail.get("collectors", []) if reboot_result.status != "pass" else [],
                tags=case.tags,
            )

        # 执行用例：先标记输出边界，确保只采集本命令之后的输出
        # 仅包裹命令执行与断言求值；依赖检查（skip 逻辑）已在上游完成，不进入 try
        try:
            start = time.monotonic()
            if case.command:
                boundary = self.transport.mark_output_boundary()
                self.transport.send_line(case.command)
                capture = self.transport.capture_since(
                    boundary, capture_timeout, recent_limit, prompt_markers
                )
            else:
                # 无命令：仅探测当前缓冲之后的输出
                boundary = self.transport.mark_output_boundary()
                capture = self.transport.capture_since(
                    boundary, capture_timeout, recent_limit, prompt_markers
                )

            output_lines = [line.text for line in capture.lines]
            prompt_visible = capture.prompt_visible
            output_text = "\n".join(output_lines)
            duration = round(time.monotonic() - start, 3)

            # 求值断言
            ctx = AssertionContext(
                output=output_text,
                prompt_visible=prompt_visible,
                exit_code=capture.exit_code,
            )
            result = self.engine.evaluate(case.assert_spec, ctx)
        except OSError as exc:
            # 传输层异常：标记为 error，避免 suite 崩溃
            return TestCaseResult(
                id=case.id,
                suite=case.suite,
                status="error",
                command=case.command,
                failure_reason=str(exc),
                error_type="transport_error",
                tags=case.tags,
            )
        except Exception as exc:
            # 其他运行时异常：记录异常类名，标记为 error
            return TestCaseResult(
                id=case.id,
                suite=case.suite,
                status="error",
                command=case.command,
                failure_reason=str(exc),
                error_type=type(exc).__name__,
                tags=case.tags,
            )

        # 构建 output_preview（前 5 行）
        preview = " | ".join(output_lines[:5]) if output_lines else ""

        if result.passed:
            return TestCaseResult(
                id=case.id,
                suite=case.suite,
                status="pass",
                command=case.command,
                output=output_text,
                output_preview=preview,
                assertion=case.assert_spec,
                duration_sec=duration,
                tags=case.tags,
            )

        return TestCaseResult(
            id=case.id,
            suite=case.suite,
            status="fail",
            command=case.command,
            output=output_text,
            output_preview=preview,
            assertion=case.assert_spec,
            duration_sec=duration,
            failure_reason=result.reason,
            triggered_collectors=case.on_fail.get("collectors", []),
            tags=case.tags,
        )

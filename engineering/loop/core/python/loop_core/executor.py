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
from loop_core.models import EvidenceBundle, TestCaseResult


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
    ) -> EvidenceBundle:
        """执行完整用例集。

        Args:
            suite: 加载后的 CaseSuite
            device_id: 设备标识
            prompt_markers: prompt 标记列表（用于 prompt_visible 断言）
            capture_timeout: 用例命令的采集超时
            recent_limit: 采集行数上限

        Returns:
            EvidenceBundle
        """
        prompt_markers = prompt_markers or []
        results: dict[str, TestCaseResult] = {}
        triggered_collectors: set[str] = set()

        for case in suite.cases:
            result = self._execute_case(
                case, results, prompt_markers, capture_timeout, recent_limit
            )
            results[case.id] = result
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
                    evidence[cname] = collector_runner.run(
                        cname,
                        suite.collectors[cname],
                        capture_timeout=capture_timeout,
                        recent_limit=recent_limit,
                    )

        # 统计
        case_list = list(results.values())
        passed = sum(1 for r in case_list if r.status == "pass")
        failed = sum(1 for r in case_list if r.status == "fail")
        skipped = sum(1 for r in case_list if r.status == "skipped")
        critical_failed = sum(
            1 for r, c in zip(case_list, suite.cases)
            if r.status == "fail" and c.severity == "critical"
        ) if case_list else 0
        overall = "PASS" if critical_failed == 0 else "FAIL"

        return EvidenceBundle(
            bundle_id=f"eb-{uuid4().hex[:8]}",
            device_id=device_id,
            suite=suite.name,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            summary={
                "total": len(case_list),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "overall": overall,
            },
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

        # 执行用例
        start = time.monotonic()
        output_lines: list[str] = []
        prompt_visible = False

        if case.command:
            # 有命令：send + capture
            self.transport.send_line(case.command)
            captured = self.transport.capture_window(
                timeout_sec=capture_timeout, recent_limit=recent_limit
            )
            output_lines = [line.text for line in captured]
        else:
            # 无命令：仅探测 prompt（capture 当前缓冲）
            captured = self.transport.capture_window(
                timeout_sec=capture_timeout, recent_limit=recent_limit
            )
            output_lines = [line.text for line in captured]

        # 检测 prompt
        for line in output_lines:
            if any(marker in line for marker in prompt_markers):
                prompt_visible = True
                break

        output_text = "\n".join(output_lines)
        duration = round(time.monotonic() - start, 3)

        # 求值断言
        ctx = AssertionContext(
            output=output_text,
            prompt_visible=prompt_visible,
        )
        result = self.engine.evaluate(case.assert_spec, ctx)

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

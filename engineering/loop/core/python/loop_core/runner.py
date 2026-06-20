"""通用 LoopRunner：场景无关，纯用例驱动。

所有场景（boot-success/lcview/lciod）共用此 runner。
新场景只需写 YAML 用例，零 Python 代码。
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loop_core.assertion_engine import AssertionEngine
from loop_core.case_loader import CaseSuite
from loop_core.executor import CaseExecutor
from loop_core.models import EvidenceBundle


class LoopRunner:
    """通用 LE 执行器。

    消费 CaseSuite + transport，产出 EvidenceBundle。
    不含任何场景特有逻辑。

    Attributes:
        device_id: 设备标识
        prompt_markers: prompt 标记列表
        transport: 实现 BaseTransport 接口的实例
        suite: 加载后的 CaseSuite
        capture_timeout: 用例命令采集超时（秒）
        recent_limit: 采集行数上限
        device_profile: 设备 profile 摘要 dict（透传给 bundle），可选
    """

    def __init__(
        self,
        device_id: str,
        prompt_markers: list[str],
        transport,
        suite: CaseSuite,
        capture_timeout: float = 5.0,
        recent_limit: int = 400,
        device_profile: dict | None = None,
    ) -> None:
        self.device_id = device_id
        self.prompt_markers = prompt_markers
        self.transport = transport
        self.suite = suite
        self.capture_timeout = capture_timeout
        self.recent_limit = recent_limit
        self.device_profile = device_profile or {}
        self.executor = CaseExecutor(transport, AssertionEngine())

    def run(self) -> EvidenceBundle:
        """执行用例集并返回证据包。

        Returns:
            EvidenceBundle（已填充 execution_config 与 device_profile 摘要）
        """
        if not self.transport.acquire_writer():
            return self.build_failure_bundle("writer busy")

        try:
            bundle = self.executor.execute_suite(
                self.suite,
                device_id=self.device_id,
                prompt_markers=self.prompt_markers,
                capture_timeout=self.capture_timeout,
                recent_limit=self.recent_limit,
            )
            self._enrich_bundle(bundle)
            return bundle
        finally:
            self.transport.release()

    def build_failure_bundle(self, reason: str) -> EvidenceBundle:
        """构建执行失败时的 EvidenceBundle（writer 抢占失败 / 运行时异常兜底）。

        公开供 CLI 顶层 try/except 兜底调用，保证任何异常下都能产出 bundle。

        Args:
            reason: 失败原因描述

        Returns:
            EvidenceBundle（已填充 device_profile / execution_config 摘要）
        """
        bundle = EvidenceBundle(
            bundle_id=f"eb-{uuid4().hex[:8]}",
            device_id=self.device_id,
            suite=self.suite.name,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            summary={
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "overall": "FAIL",
                "error": reason,
            },
            cases=[],
            evidence={},
        )
        self._enrich_bundle(bundle)
        return bundle

    def _enrich_bundle(self, bundle: EvidenceBundle) -> None:
        """填充 bundle 的 device_profile 与 execution_config 摘要。"""
        bundle.device_profile = {
            "device_id": self.device_id,
            "prompt_markers": self.prompt_markers,
            **self.device_profile,  # 调用方注入的 profile 字段优先
        }
        bundle.execution_config = {
            "capture_timeout": self.capture_timeout,
            "recent_limit": self.recent_limit,
            "provider_type": type(self.transport).__name__,
        }

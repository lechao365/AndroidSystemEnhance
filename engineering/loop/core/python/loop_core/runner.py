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
    """

    def __init__(
        self,
        device_id: str,
        prompt_markers: list[str],
        transport,
        suite: CaseSuite,
        capture_timeout: float = 5.0,
        recent_limit: int = 400,
    ) -> None:
        self.device_id = device_id
        self.prompt_markers = prompt_markers
        self.transport = transport
        self.suite = suite
        self.capture_timeout = capture_timeout
        self.recent_limit = recent_limit
        self.executor = CaseExecutor(transport, AssertionEngine())

    def run(self) -> EvidenceBundle:
        """执行用例集并返回证据包。

        Returns:
            EvidenceBundle
        """
        if not self.transport.acquire_writer():
            return self._build_failure_bundle("writer busy")

        try:
            return self.executor.execute_suite(
                self.suite,
                device_id=self.device_id,
                prompt_markers=self.prompt_markers,
                capture_timeout=self.capture_timeout,
                recent_limit=self.recent_limit,
            )
        finally:
            self.transport.release()

    def _build_failure_bundle(self, reason: str) -> EvidenceBundle:
        """构建 writer 获取失败时的 EvidenceBundle。"""
        return EvidenceBundle(
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

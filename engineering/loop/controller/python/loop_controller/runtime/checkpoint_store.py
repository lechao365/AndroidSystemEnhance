from __future__ import annotations

import json
import logging
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import CheckpointRecord

_logger = logging.getLogger("loop_runtime_checkpoint")

_CHECKPOINT_FILENAME = "runtime_checkpoints.jsonl"


class CheckpointStore:
    def __init__(self, artifacts_dir: str, session_id: str) -> None:
        self._path = Path(artifacts_dir) / _CHECKPOINT_FILENAME
        self._session_id = session_id

    def save(self, cp: CheckpointRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(cp.to_dict(), ensure_ascii=False) + "\n")

    def latest(self) -> CheckpointRecord | None:
        if not self._path.exists():
            return None
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return None
        # Scan from the end for the latest checkpoint belonging to this session.
        for line in reversed(lines):
            if not line:
                continue
            cp = self._from_line(line)  # 坏行返回 None，跳过（P2-1）
            if cp is not None and cp.session_id == self._session_id:
                return cp
        return None

    def all(self) -> list[CheckpointRecord]:
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        results: list[CheckpointRecord] = []
        for line in lines:
            if not line:
                continue
            cp = self._from_line(line)  # 坏行返回 None，跳过（P2-1）
            if cp is not None and cp.session_id == self._session_id:
                results.append(cp)
        return results

    def _from_line(self, line: str) -> CheckpointRecord | None:
        """解析一行 JSONL 为 CheckpointRecord。坏行记录 warning 并返回 None。

        P2-1：原实现对坏行抛 JSONDecodeError，单条坏行使 latest()/all() 全部不可用。
        改为容错：跳过坏行并记录诊断日志，不影响其他有效 checkpoint 的读取。
        """
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError) as e:
            _logger.warning("跳过损坏的 checkpoint 行（%s）: %s", e, line[:120])
            return None
        return CheckpointRecord(
            checkpoint_id=data["checkpoint_id"],
            session_id=data["session_id"],
            attempt_index=data["attempt_index"],
            current_node=data["current_node"],
            input_summary=data.get("input_summary", {}),
            output_summary=data.get("output_summary", {}),
            failure_code=FailureCode(data.get("failure_code", "NONE")),
            matched_guards=data.get("matched_guards", []),
            next_node=data["next_node"],
            timestamp=data["timestamp"],
        )

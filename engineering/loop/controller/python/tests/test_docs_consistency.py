"""文档一致性元测试：守护 contracts/controller README 与实现层真相不漂移。

借鉴 test_diagnosis_contract_docs.py 的 _repo_root / _read helper 模式。
新增/删除 FailureCode、guard、NodeKind、contracts 导出符号时，必须同步改 README。
"""
from pathlib import Path

from loop_contracts import __all__ as _contracts_all
from loop_contracts.failure_codes import FailureCode
from loop_controller.runtime.guards import _GUARD_REGISTRY
from loop_controller.runtime.types import NodeKind


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    while path.name != "engineering":
        if path == path.parent:
            raise RuntimeError("engineering/ root not found")
        path = path.parent
    return path.parent


def _read(relative_path: str) -> str:
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


# ---------- contracts/README.md 守护 ----------

def test_failure_code_count_matches_readme() -> None:
    """守护点 1: FailureCode 成员数 = 17，README 必须含 '17 项'。"""
    count = len(list(FailureCode))
    assert count == 17, f"FailureCode 成员数变了: {count}，请同步改此测试和 README"
    text = _read("engineering/loop/contracts/README.md")
    assert "17 项" in text, "contracts/README.md 缺少 '17 项'，请同步更新"


def test_failure_code_names_in_readme() -> None:
    """守护点 2: 每个 FailureCode 成员名都出现在 README 中。"""
    text = _read("engineering/loop/contracts/README.md")
    missing = [name for name in FailureCode.__members__ if name not in text]
    assert not missing, f"contracts/README.md 缺少这些 FailureCode 名: {missing}"


def test_contracts_all_count_matches_readme() -> None:
    """守护点 3: contracts __all__ 长度 = 9，README 必须含 '九符号'。"""
    count = len(_contracts_all)
    assert count == 9, f"contracts __all__ 长度变了: {count}，请同步改此测试和 README"
    text = _read("engineering/loop/contracts/README.md")
    assert "九符号" in text or "9" in text, "contracts/README.md 缺少导出符号数量说明"


def test_contracts_all_names_in_readme() -> None:
    """守护点 4: 每个 contracts 导出符号名都出现在 README 中。"""
    text = _read("engineering/loop/contracts/README.md")
    missing = [name for name in _contracts_all if name not in text]
    assert not missing, f"contracts/README.md 缺少这些导出符号名: {missing}"


def test_contracts_dataclass_count_matches_readme() -> None:
    """守护点 5: dataclass 数 = 6，README 必须含 '六 dataclass'。"""
    text = _read("engineering/loop/contracts/README.md")
    assert "六 dataclass" in text or "6" in text, "contracts/README.md 缺少 dataclass 数量说明"


# ---------- controller/README.md 守护 ----------

def test_guards_count_matches_readme() -> None:
    """守护点 6: guard 数量 = 16，README 必须含 '16 个'。"""
    count = len(_GUARD_REGISTRY)
    assert count == 16, f"guard 数量变了: {count}，请同步改此测试和 README"
    text = _read("engineering/loop/controller/README.md")
    assert "16 个" in text, "controller/README.md 缺少 '16 个'，请同步更新"


def test_guards_names_in_readme() -> None:
    """守护点 7: 每个 guard 名都出现在 controller README 中。"""
    text = _read("engineering/loop/controller/README.md")
    missing = [name for name in _GUARD_REGISTRY if name not in text]
    assert not missing, f"controller/README.md 缺少这些 guard 名: {missing}"


def test_nodekind_names_in_readme() -> None:
    """守护点 8: 每个 NodeKind 成员名都出现在 controller README 中。"""
    text = _read("engineering/loop/controller/README.md")
    missing = [name for name in NodeKind.__members__ if name not in text]
    assert not missing, f"controller/README.md 缺少这些 NodeKind 名: {missing}"

from enum import StrEnum

from loop_contracts.failure_codes import FailureCode


def test_verification_regression_member_exists_with_value():
    assert hasattr(FailureCode, "VERIFICATION_REGRESSION")
    assert FailureCode.VERIFICATION_REGRESSION == "VERIFICATION_REGRESSION"


def test_verification_stuck_member_exists_with_value():
    assert hasattr(FailureCode, "VERIFICATION_STUCK")
    assert FailureCode.VERIFICATION_STUCK == "VERIFICATION_STUCK"


def test_evidence_fail_member_exists_with_value():
    assert hasattr(FailureCode, "EVIDENCE_FAIL")
    assert FailureCode.EVIDENCE_FAIL == "EVIDENCE_FAIL"


def test_verification_members_are_str_enum_instances():
    assert isinstance(FailureCode.VERIFICATION_REGRESSION, FailureCode)
    assert isinstance(FailureCode.VERIFICATION_STUCK, FailureCode)
    assert isinstance(FailureCode.VERIFICATION_REGRESSION, StrEnum)
    assert isinstance(FailureCode.VERIFICATION_STUCK, StrEnum)


def test_verification_members_constructible_by_value():
    assert FailureCode("VERIFICATION_REGRESSION") is FailureCode.VERIFICATION_REGRESSION
    assert FailureCode("VERIFICATION_STUCK") is FailureCode.VERIFICATION_STUCK


def test_wall_clock_budget_exceeded_exists():
    """G5: 新增 WALL_CLOCK_BUDGET_EXCEEDED FailureCode。"""
    from loop_contracts.failure_codes import FailureCode
    assert hasattr(FailureCode, "WALL_CLOCK_BUDGET_EXCEEDED")
    assert FailureCode.WALL_CLOCK_BUDGET_EXCEEDED.value == "WALL_CLOCK_BUDGET_EXCEEDED"


def test_failure_code_count_is_18():
    """G5: FailureCode 成员数应为 18（原 17 + WALL_CLOCK_BUDGET_EXCEEDED）。"""
    from loop_contracts.failure_codes import FailureCode
    assert len(list(FailureCode)) == 18

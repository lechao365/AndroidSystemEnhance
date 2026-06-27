from enum import StrEnum

from loop_contracts.failure_codes import FailureCode


def test_verification_regression_member_exists_with_value():
    assert hasattr(FailureCode, "VERIFICATION_REGRESSION")
    assert FailureCode.VERIFICATION_REGRESSION == "VERIFICATION_REGRESSION"


def test_verification_stuck_member_exists_with_value():
    assert hasattr(FailureCode, "VERIFICATION_STUCK")
    assert FailureCode.VERIFICATION_STUCK == "VERIFICATION_STUCK"


def test_verification_members_are_str_enum_instances():
    assert isinstance(FailureCode.VERIFICATION_REGRESSION, FailureCode)
    assert isinstance(FailureCode.VERIFICATION_STUCK, FailureCode)
    assert isinstance(FailureCode.VERIFICATION_REGRESSION, StrEnum)
    assert isinstance(FailureCode.VERIFICATION_STUCK, StrEnum)


def test_verification_members_constructible_by_value():
    assert FailureCode("VERIFICATION_REGRESSION") is FailureCode.VERIFICATION_REGRESSION
    assert FailureCode("VERIFICATION_STUCK") is FailureCode.VERIFICATION_STUCK

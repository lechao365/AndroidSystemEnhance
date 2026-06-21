from loop_workflows.builtin import MultiPhaseVerifyWorkflow, SingleRunVerifyWorkflow


def test_single_run_verify_exposes_workflow_id():
    workflow = SingleRunVerifyWorkflow()
    assert workflow.workflow_id == "single_run_verify"


def test_multi_phase_verify_exposes_expected_phases():
    workflow = MultiPhaseVerifyWorkflow()
    assert workflow.workflow_id == "multi_phase_verify"
    assert workflow.phases == ["bootstrap", "feature", "fallback"]

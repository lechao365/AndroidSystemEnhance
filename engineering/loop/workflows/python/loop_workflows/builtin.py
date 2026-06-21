from loop_workflows.base import WorkflowDefinition


class SingleRunVerifyWorkflow(WorkflowDefinition):
    def __init__(self) -> None:
        super().__init__(workflow_id="single_run_verify", phases=["run", "verify"])


class MultiPhaseVerifyWorkflow(WorkflowDefinition):
    def __init__(self) -> None:
        super().__init__(workflow_id="multi_phase_verify", phases=["bootstrap", "feature", "fallback"])

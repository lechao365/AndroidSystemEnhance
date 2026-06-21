from dataclasses import dataclass, field


@dataclass
class WorkflowDefinition:
    workflow_id: str
    phases: list[str] = field(default_factory=list)

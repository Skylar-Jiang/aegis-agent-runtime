from collections.abc import Mapping

from pydantic import Field, model_validator

from .common import ContractModel, UTCDateTime
from .execution import ToolExecutionResult
from .tools import ToolCallRequest


class TaskNode(ContractModel):
    task_id: str = Field(min_length=1)
    graph_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    request: ToolCallRequest
    dependencies: list[str] = Field(default_factory=list)
    parallel_safe: bool
    effect_targets: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_request_scope(self) -> "TaskNode":
        if self.request.task_id != self.task_id:
            raise ValueError("node request task_id must match node task_id")
        if self.node_id in self.dependencies:
            raise ValueError("node cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("node dependencies must be unique")
        return self


class TaskGraph(ContractModel):
    graph_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    nodes: list[TaskNode] = Field(min_length=1)
    max_parallelism: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_nodes(self) -> "TaskGraph":
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("task graph node_ids must be unique")
        if any(node.task_id != self.task_id for node in self.nodes):
            raise ValueError("all task graph nodes must belong to graph task_id")
        if any(node.graph_id != self.graph_id for node in self.nodes):
            raise ValueError("all task graph nodes must belong to graph graph_id")
        if any(
            dependency not in node_ids
            for node in self.nodes
            for dependency in node.dependencies
        ):
            raise ValueError("task graph dependencies must reference graph nodes")
        self._ensure_acyclic({node.node_id: node.dependencies for node in self.nodes})
        return self

    @staticmethod
    def _ensure_acyclic(dependencies: Mapping[str, list[str]]) -> None:
        remaining = {
            node_id: set(node_dependencies)
            for node_id, node_dependencies in dependencies.items()
        }
        while remaining:
            ready = {
                node_id
                for node_id, node_dependencies in remaining.items()
                if not node_dependencies
            }
            if not ready:
                raise ValueError("task graph dependencies must be acyclic")
            remaining = {
                node_id: node_dependencies - ready
                for node_id, node_dependencies in remaining.items()
                if node_id not in ready
            }


class TaskGraphResult(ContractModel):
    graph_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    node_results: dict[str, ToolExecutionResult] = Field(default_factory=dict)
    blocked_nodes: dict[str, str] = Field(default_factory=dict)
    started_at: UTCDateTime | None = None
    finished_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def validate_result_scope(self) -> "TaskGraphResult":
        if any(result.task_id != self.task_id for result in self.node_results.values()):
            raise ValueError("task graph results must belong to graph task_id")
        if set(self.node_results) & set(self.blocked_nodes):
            raise ValueError("completed and blocked node ids must not overlap")
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("task graph finished_at must not precede started_at")
        return self

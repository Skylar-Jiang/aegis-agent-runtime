from typing import Protocol

from pydantic import ValidationError

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult, ToolSpec

from .arguments import TOOL_ARGUMENT_MODELS


class ToolHandler(Protocol):
    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult: ...


class MockToolHandler:
    """No-side-effect handler used only to validate Registry integration."""

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            output={"mock": True, "tool_name": request.tool_name},
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler | None = None) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        if handler is not None:
            self._handlers[spec.name] = handler

    def get(self, name: str) -> ToolSpec:
        return self.get_spec(name)

    def get_spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as error:
            raise KeyError(f"Unknown tool: {name}") from error

    def get_handler(self, name: str) -> ToolHandler:
        if name not in self._specs:
            raise KeyError(f"Unknown tool: {name}")
        try:
            return self._handlers[name]
        except KeyError as error:
            raise LookupError(f"Tool has no handler: {name}") from error

    def contains(self, name: str) -> bool:
        return name in self._specs

    def list_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def planner_tools(self) -> list[dict[str, object]]:
        """Return Runtime-owned tool metadata safe to expose to a planner."""

        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "base_risk": spec.base_risk.value,
                "side_effect_type": spec.side_effect_type,
                "required_permissions": [item.value for item in spec.required_permissions],
            }
            for spec in self.list_specs()
        ]

    def validate_arguments(self, name: str, arguments: object) -> dict[str, object]:
        """Validate untrusted planner arguments before risk or policy evaluation."""

        if name not in self._specs:
            raise KeyError(f"Unknown tool: {name}")
        model = TOOL_ARGUMENT_MODELS.get(name)
        if model is None:
            raise ValueError(f"Tool has no registered argument model: {name}")
        try:
            validated = model.model_validate(arguments)
        except ValidationError as error:
            raise ValueError(f"Invalid arguments for {name}: {error}") from error
        return validated.model_dump(mode="json", exclude_none=True)

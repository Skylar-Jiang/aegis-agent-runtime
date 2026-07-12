from typing import Protocol

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult, ToolSpec


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

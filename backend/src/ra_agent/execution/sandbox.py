class SandboxExecutor:
    """Phase 0 placeholder; a temporary directory is not a system sandbox."""

    async def execute(self, _request: object) -> None:
        raise NotImplementedError("Real sandbox execution is outside Phase 0")

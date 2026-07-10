class MemoryGuard:
    async def stage(self, _value: object) -> None:
        raise NotImplementedError("Trusted/pending memory is outside Phase 0")

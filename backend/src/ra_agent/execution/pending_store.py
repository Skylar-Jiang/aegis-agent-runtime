class PendingStore:
    """Interface placeholder for untrusted intermediate results."""

    async def put(self, _key: str, _value: object) -> None:
        raise NotImplementedError("Pending persistence is outside Phase 0")

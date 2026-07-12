class RuleEngine:
    """Interface placeholder for YAML-backed rules."""

    def evaluate(self, _context: dict[str, object]) -> None:
        raise NotImplementedError("Rule evaluation is scheduled after Phase 1")

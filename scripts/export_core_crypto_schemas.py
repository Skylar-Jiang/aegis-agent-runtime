"""Regenerate the P3 JSON Schemas (no private keys or application startup)."""

import json
from pathlib import Path

from ra_agent.audit import AuditCheckpoint, VerificationResult
from ra_agent.audit.integrity import AuditBundle, CheckpointRecord
from ra_agent.crypto import SignedEnvelope


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "docs/interfaces/schemas"
    output.mkdir(parents=True, exist_ok=True)
    for model in (
        SignedEnvelope,
        AuditCheckpoint,
        CheckpointRecord,
        AuditBundle,
        VerificationResult,
    ):
        (output / f"{model.__name__}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

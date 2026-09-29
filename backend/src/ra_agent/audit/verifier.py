from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ra_agent.crypto import CryptoError, EnvelopeService, signed_object_digest
from ra_agent.crypto.canonical import CRYPTO_UNAVAILABLE_CODES

from .checkpoints import CheckpointStore, validate_checkpoint
from .integrity import AuditBundle, HashChain, ensure_bundle_size

VERIFICATION_UNAVAILABLE_CODES = CRYPTO_UNAVAILABLE_CODES


class VerificationIssue(BaseModel):
    code: str
    message: str


class VerificationResult(BaseModel):
    valid: bool
    verified_events: int = 0
    anchored_from_seq: int | None = None
    anchored_to_seq: int | None = None
    tail_complete: bool = False
    errors: list[VerificationIssue] = Field(default_factory=list)


class AuditVerifier:
    def __init__(
        self,
        envelopes: EnvelopeService,
        checkpoints: CheckpointStore,
        *,
        allowed_object_types: frozenset[str] = frozenset(
            {
                "TaskContractV2",
                "ToolCallEnvelope",
                "GatewayDecision",
                "ToolResult",
            }
        ),
    ) -> None:
        self.envelopes = envelopes
        self.checkpoints = checkpoints
        self.allowed_object_types = allowed_object_types

    def verify_bundle(
        self, bundle: dict[str, Any], *, trusted_checkpoint_id: str, task_id: str
    ) -> VerificationResult:
        """No network, database or private key needed; caller provides trust context."""
        try:
            return self._verify(
                bundle, trusted_checkpoint_id=trusted_checkpoint_id, task_id=task_id
            )
        except CryptoError as exc:
            return VerificationResult(
                valid=False,
                errors=[
                    VerificationIssue(
                        code=exc.code,
                        message=str(exc),
                    )
                ],
            )
        except (ValidationError, TypeError, ValueError, RecursionError):
            return VerificationResult(
                valid=False,
                errors=[
                    VerificationIssue(
                        code="BUNDLE_INVALID",
                        message="Malformed or unsupported Core v1 evidence bundle",
                    )
                ],
            )
        except OSError:
            return VerificationResult(
                valid=False,
                errors=[
                    VerificationIssue(
                        code="CHECK_UNAVAILABLE",
                        message="Verifier storage unavailable",
                    )
                ],
            )

    def _verify(
        self, bundle: dict[str, Any], *, trusted_checkpoint_id: str, task_id: str
    ) -> VerificationResult:
        ensure_bundle_size(bundle)
        parsed = AuditBundle.model_validate(bundle)
        trusted = self.checkpoints.get_trusted_checkpoint(trusted_checkpoint_id)
        validate_checkpoint(trusted, self.envelopes)
        if parsed.task_id != task_id or trusted.task_id != task_id:
            raise CryptoError("TASK_MISMATCH")
        if (
            parsed.checkpoint.model_dump() != trusted.checkpoint.model_dump()
            or parsed.signed_checkpoint.model_dump() != trusted.signed_checkpoint.model_dump()
        ):
            raise CryptoError("ANCHOR_MISMATCH")
        if len(parsed.entries) != trusted.checkpoint.to_seq:
            raise CryptoError("CHECKPOINT_RANGE_INVALID")
        chain = HashChain(task_id)
        references: set[str] = set()
        for entry in parsed.entries:
            expected = chain.append_event(entry.event)
            if expected != entry.model_dump():
                raise CryptoError("CHAIN_INVALID", f"event sequence {entry.event['sequence']}")
            for field in ("object_digest", "result_digest"):
                if entry.event[field] is not None:
                    references.add(entry.event[field])
        if chain.get_chain_head() != trusted.checkpoint.chain_head:
            raise CryptoError("CHAIN_HEAD_MISMATCH")
        objects: set[str] = set()
        for obj in parsed.objects:
            env = obj.envelope
            if env.object_type not in self.allowed_object_types:
                raise CryptoError("OBJECT_TYPE_INVALID")
            if not self.envelopes.verify(obj.payload, env, object_type=env.object_type):
                raise CryptoError("SIGNATURE_INVALID", "invalid referenced object")
            if "task_id" in obj.payload and obj.payload["task_id"] != task_id:
                raise CryptoError("TASK_MISMATCH", "signed object belongs to another task")
            reference = signed_object_digest(obj.payload, env)
            if reference in objects:
                raise CryptoError("OBJECT_DUPLICATE")
            objects.add(reference)
        if references - objects:
            raise CryptoError("OBJECT_MISSING")
        if objects - references:
            raise CryptoError("OBJECT_UNREFERENCED")
        return VerificationResult(
            valid=True,
            verified_events=len(parsed.entries),
            anchored_from_seq=1,
            anchored_to_seq=trusted.checkpoint.to_seq,
        )

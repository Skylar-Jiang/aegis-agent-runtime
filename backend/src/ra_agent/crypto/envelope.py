from __future__ import annotations

import hmac
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ra_agent.gateway.interfaces import SignatureProvider

from .canonical import CRYPTO_UNAVAILABLE_CODES, Canonicalizer, CryptoError
from .openssl import DigestProvider

HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
SCHEMA_VERSION = "1.0"
ALGORITHM = "SM2-SM3"
ENVELOPE_DOMAIN = b"AEGIS-CORE:SIGNED-ENVELOPE:v1\x00"


class SignedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    object_type: Identifier
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    algorithm: Literal["SM2-SM3"] = ALGORITHM
    key_id: Identifier
    payload_digest: HexDigest
    signature: Annotated[str, Field(min_length=88, max_length=100)]


def signed_object_digest(payload: Any, envelope: SignedEnvelope | dict[str, Any]) -> str:
    """Event reference to the exact signed object, including type/key/signature metadata."""
    env = SignedEnvelope.model_validate(
        envelope.model_dump() if isinstance(envelope, SignedEnvelope) else envelope,
    )
    return DigestProvider().sm3(
        Canonicalizer().canonicalize(
            {
                "payload": payload,
                "envelope": env.model_dump(),
            }
        )
    )


class EnvelopeService:
    def __init__(self, signatures: SignatureProvider) -> None:
        self.signatures = signatures
        self.canonicalizer = Canonicalizer()
        self.digests = DigestProvider()

    def sign(self, payload: Any, *, object_type: str, key_id: str) -> SignedEnvelope:
        metadata = {
            "object_type": object_type,
            "schema_version": SCHEMA_VERSION,
            "algorithm": ALGORITHM,
            "key_id": key_id,
            "payload_digest": self.digests.sm3(self.canonicalizer.canonicalize(payload)),
        }
        # Validate before signing anything; the six wire fields remain the plan's fields.
        SignedEnvelope.model_validate(metadata | {"signature": "A" * 88})
        signature = self.signatures.sign_sm2(
            ENVELOPE_DOMAIN + self.canonicalizer.canonicalize(metadata),
            key_id=key_id,
        )
        return SignedEnvelope.model_validate(metadata | {"signature": signature})

    def verify(
        self, payload: Any, envelope: SignedEnvelope | dict[str, Any], *, object_type: str
    ) -> bool:
        try:
            # Revalidate even model instances (model_copy/model_construct can bypass validation).
            env = SignedEnvelope.model_validate(
                envelope.model_dump() if isinstance(envelope, SignedEnvelope) else envelope,
            )
            if env.object_type != object_type:
                return False
            digest = self.digests.sm3(self.canonicalizer.canonicalize(payload))
            if not hmac.compare_digest(env.payload_digest, digest):
                return False
            metadata = env.model_dump(exclude={"signature"})
            return self.signatures.verify_sm2(
                ENVELOPE_DOMAIN + self.canonicalizer.canonicalize(metadata),
                env.signature,
                key_id=env.key_id,
            )
        except CryptoError as exc:
            if exc.code in CRYPTO_UNAVAILABLE_CODES:
                raise
            return False
        except (ValidationError, TypeError):
            return False

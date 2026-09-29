"""OpenSSL 3 SM2/SM3 adapter. No handwritten curve arithmetic or nonce generation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .canonical import CryptoError

SM2_ID = "1234567812345678"
_SM2_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a811ccf5501822d03420004")


def find_openssl() -> str:
    explicit = os.environ.get("AEGIS_OPENSSL")
    candidates = [explicit] if explicit else [shutil.which("openssl")]
    if not explicit and os.name == "nt":
        candidates.append(
            str(
                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
            )
        )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise CryptoError("CHECK_UNAVAILABLE", "OpenSSL 3 required; set AEGIS_OPENSSL")


def _run(executable: str, args: list[str], data: bytes = b"") -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [executable, *args],
            input=data,
            capture_output=True,
            check=False,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CryptoError("CHECK_UNAVAILABLE", "OpenSSL invocation failed") from exc


def _checked(executable: str, args: list[str], data: bytes = b"") -> bytes:
    result = _run(executable, args, data)
    if result.returncode:
        # Never propagate process output containing key material or private paths.
        raise CryptoError("CHECK_UNAVAILABLE", "OpenSSL operation failed")
    return result.stdout


class DigestProvider:
    def sm3(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise CryptoError("INPUT_INVALID", "sm3 requires bytes")
        try:
            return hashlib.new("sm3", payload).hexdigest()
        except ValueError:
            return _checked(find_openssl(), ["dgst", "-sm3", "-binary"], payload).hex()


def generate_sm2_key(private_key_path: Path) -> str:
    """Provision an unencrypted local key with exclusive creation; return SPKI PEM.

    The caller must protect the containing directory with OS ACLs (especially Windows).
    Production key isolation requires a separate signer account/service or HSM.
    """
    executable = find_openssl()
    key = _checked(executable, ["genpkey", "-algorithm", "SM2"])
    public = _checked(executable, ["pkey", "-pubout"], key).decode("ascii")
    try:
        fd = os.open(private_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise CryptoError("KEY_STORAGE_ERROR", "key file must be a new writable path") from exc
    return public


class OpenSSLSignatureProvider:
    """Implements gateway.interfaces.SignatureProvider. Signatures are base64(DER).

    Public keys are pinned by the caller, never learned from an evidence bundle.
    Retain old public keys for normal rotation; remove compromised keys from trust.
    """

    def __init__(
        self, public_keys: dict[str, str], *, private_keys: dict[str, Path] | None = None
    ) -> None:
        self._executable = find_openssl()
        version = _checked(self._executable, ["version"])
        if not version.startswith(b"OpenSSL 3."):
            raise CryptoError("CHECK_UNAVAILABLE", "this adapter requires OpenSSL 3.x")
        self._public: dict[str, str] = {}
        self._private = {key: Path(path).resolve() for key, path in (private_keys or {}).items()}
        for key_id, pem in public_keys.items():
            if not isinstance(key_id, str) or not key_id or len(key_id) > 128:
                raise CryptoError("KEY_INVALID", "invalid key_id")
            try:
                encoded = pem.encode("ascii")
                der = _checked(self._executable, ["pkey", "-pubin", "-outform", "DER"], encoded)
                if len(der) != 91 or not der.startswith(_SM2_SPKI_PREFIX):
                    raise CryptoError("KEY_INVALID", "SM2 public key required")
                _checked(self._executable, ["pkey", "-pubin", "-pubcheck", "-noout"], encoded)
                self._public[key_id] = _checked(
                    self._executable,
                    ["pkey", "-pubin", "-pubout"],
                    encoded,
                ).decode("ascii")
            except (AttributeError, UnicodeError) as exc:
                raise CryptoError("KEY_INVALID") from exc
        for key_id, path in self._private.items():
            if key_id not in self._public:
                raise CryptoError("KEY_UNKNOWN")
            derived = _checked(self._executable, ["pkey", "-in", str(path), "-pubout"])
            if derived.decode("ascii") != self._public[key_id]:
                raise CryptoError("KEY_MISMATCH")

    def list_public_keys(self) -> dict[str, str]:
        return dict(self._public)

    def sign_sm2(self, payload: bytes, *, key_id: str) -> str:
        if not isinstance(payload, bytes):
            raise CryptoError("INPUT_INVALID", "sign_sm2 requires bytes")
        if key_id not in self._private:
            raise CryptoError("KEY_UNAVAILABLE", "no signing key for key_id")
        signature = _checked(
            self._executable,
            [
                "pkeyutl",
                "-sign",
                "-rawin",
                "-digest",
                "sm3",
                "-inkey",
                str(self._private[key_id]),
                "-pkeyopt",
                f"distid:{SM2_ID}",
            ],
            payload,
        )
        result = base64.b64encode(signature).decode("ascii")
        # Fail closed if a private key file changed since construction.
        if not self.verify_sm2(payload, result, key_id=key_id):
            raise CryptoError("KEY_MISMATCH")
        return result

    def verify_sm2(self, payload: bytes, signature: str, *, key_id: str) -> bool:
        if key_id not in self._public or not isinstance(payload, bytes):
            return False
        if not isinstance(signature, str) or not 88 <= len(signature) <= 100:
            return False
        try:
            der = base64.b64decode(signature, validate=True)
            if base64.b64encode(der).decode("ascii") != signature:
                return False
        except (ValueError, binascii.Error):
            return False
        try:
            return self._verify_der(payload, der, key_id)
        except OSError as exc:
            raise CryptoError(
                "CHECK_UNAVAILABLE", "verification temporary storage unavailable"
            ) from exc

    def _verify_der(self, payload: bytes, der: bytes, key_id: str) -> bool:
        with tempfile.TemporaryDirectory(prefix="aegis-sm2-") as directory:
            pub = Path(directory) / "public.pem"
            sig = Path(directory) / "signature.der"
            pub.write_text(self._public[key_id], encoding="ascii")
            sig.write_bytes(der)
            result = _run(
                self._executable,
                [
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(pub),
                    "-sigfile",
                    str(sig),
                    "-rawin",
                    "-digest",
                    "sm3",
                    "-pkeyopt",
                    f"distid:{SM2_ID}",
                ],
                payload,
            )
            return result.returncode == 0

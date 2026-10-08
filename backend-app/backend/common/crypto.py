"""common/crypto (Standard 1). The ONLY module that imports crypto primitives.

- AES-256-GCM envelope encryption: per-tenant DEK wrapped by KEK; 96-bit random nonce;
  AAD = tenant_id + object_id.  Blob layout: b"PF1" | kek_kid(8) | dek_kid(8) | wrapped_dek_len(2) |
  wrapped_dek | nonce(12) | ciphertext+tag.
- SHA-256 over canonical JSON (sorted keys, no whitespace, UTF-8, floats rejected unless finite).
- Ed25519 signatures with key ids (kid); old public keys retained so rotation never breaks history.
- HMAC-SHA256 signed URLs with expiry in the signed payload; compare via hmac.compare_digest.
- argon2id is optional (argon2-cffi); falls back to scrypt from cryptography (never plain hashes).
- Missing key => fail closed (AgentError FORBIDDEN/ENGINE_FAILED), never plaintext fallback.

Keys come from env (PF_KEK_B64, PF_SIGNING_KEY_B64, PF_HMAC_KEY_B64) or are generated in memory
for dev/test. Nothing is written to disk or logs.
"""
import base64
import hashlib
import hmac
import json
import math
import os
import threading
import time
from typing import Any

from cryptography.exceptions import InvalidKey, InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import AgentError, ErrorCode

MAGIC = b"PF1"
_lock = threading.Lock()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon(o: Any) -> Any:
    if isinstance(o, float):
        if not math.isfinite(o):
            raise AgentError(ErrorCode.INVALID_INPUT, "Non-finite number in canonical JSON")
        return o
    if isinstance(o, dict):
        return {str(k): _canon(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_canon(v) for v in o]
    return o


def canonical_json(obj: Any) -> bytes:
    return json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


class KeyRing:
    """Holds versioned KEKs, wrapped per-tenant DEKs, signing keys and the HMAC key."""

    def __init__(self) -> None:
        self.keks: dict[str, bytes] = {}
        self.active_kek: str | None = None
        self.deks: dict[tuple[str, str], tuple[str, bytes]] = {}  # (tenant, dek_kid) -> (kek_kid, wrapped)
        self.active_dek: dict[str, str] = {}
        self.signing: dict[str, Ed25519PrivateKey] = {}
        self.public: dict[str, Ed25519PublicKey] = {}
        self.active_signing: str | None = None
        self.hmac_key: bytes | None = None
        self.n = 0

    # --- key lifecycle -------------------------------------------------
    def add_kek(self, raw: bytes, kid: str | None = None, activate: bool = True) -> str:
        if len(raw) != 32:
            raise AgentError(ErrorCode.ENGINE_FAILED, "KEK must be 32 bytes")
        kid = kid or f"kek{len(self.keks) + 1}"
        self.keks[kid] = raw
        if activate:
            self.active_kek = kid
        return kid

    def add_signing_key(self, key: Ed25519PrivateKey | None = None, activate: bool = True) -> str:
        key = key or Ed25519PrivateKey.generate()
        kid = f"sig{len(self.signing) + 1}"
        self.signing[kid] = key
        self.public[kid] = key.public_key()
        if activate:
            self.active_signing = kid
        return kid

    def rotate_kek(self) -> str:
        return self.add_kek(os.urandom(32))

    def rotate_signing(self) -> str:
        return self.add_signing_key()

    def public_key_pem(self, kid: str) -> bytes:
        return self.public[kid].public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)

    def _dek(self, tenant: str, dek_kid: str | None = None) -> tuple[str, bytes]:
        if self.active_kek is None:
            raise AgentError(ErrorCode.ENGINE_FAILED, "No key encryption key configured")
        with _lock:
            if dek_kid is None:
                dek_kid = self.active_dek.get(tenant)
                if dek_kid is None:
                    self.n += 1
                    dek_kid = f"dek{self.n}"
                    raw = os.urandom(32)
                    kek = self.keks[self.active_kek]
                    nonce = os.urandom(12)
                    wrapped = nonce + AESGCM(kek).encrypt(nonce, raw, tenant.encode() + dek_kid.encode())
                    self.deks[(tenant, dek_kid)] = (self.active_kek, wrapped)
                    self.active_dek[tenant] = dek_kid
            entry = self.deks.get((tenant, dek_kid))
        if entry is None:
            raise AgentError(ErrorCode.FORBIDDEN, "Data key unavailable")
        kek_kid, wrapped = entry
        kek = self.keks.get(kek_kid)
        if kek is None:
            raise AgentError(ErrorCode.FORBIDDEN, "Key encryption key unavailable")
        try:
            raw = AESGCM(kek).decrypt(wrapped[:12], wrapped[12:], tenant.encode() + dek_kid.encode())
        except InvalidTag:
            raise AgentError(ErrorCode.FORBIDDEN, "Data key unwrap failed")
        return dek_kid, raw


def _bootstrap() -> KeyRing:
    kr = KeyRing()
    kek = os.environ.get("PF_KEK_B64")
    kr.add_kek(base64.b64decode(kek) if kek else os.urandom(32))
    sk = os.environ.get("PF_SIGNING_KEY_B64")
    kr.add_signing_key(Ed25519PrivateKey.from_private_bytes(base64.b64decode(sk)) if sk else None)
    hk = os.environ.get("PF_HMAC_KEY_B64")
    kr.hmac_key = base64.b64decode(hk) if hk else os.urandom(32)
    return kr


keyring = _bootstrap()


def _tenant() -> str:
    from . import config
    return config.get("tenant_id", "default")


def _aad(object_id: str, tenant_id: str | None) -> bytes:
    return (tenant_id or _tenant()).encode() + b"|" + object_id.encode()


def encrypt(plaintext: bytes, object_id: str, tenant_id: str | None = None) -> bytes:
    tenant = tenant_id or _tenant()
    dek_kid, dek = keyring._dek(tenant)
    kek_kid, wrapped = keyring.deks[(tenant, dek_kid)]
    nonce = os.urandom(12)
    ct = AESGCM(dek).encrypt(nonce, plaintext, _aad(object_id, tenant))
    k, d = kek_kid.encode().ljust(8, b"\0"), dek_kid.encode().ljust(8, b"\0")
    return MAGIC + k + d + len(wrapped).to_bytes(2, "big") + wrapped + nonce + ct


def decrypt(blob: bytes, object_id: str, tenant_id: str | None = None) -> bytes:
    tenant = tenant_id or _tenant()
    try:
        if blob[:3] != MAGIC:
            raise AgentError(ErrorCode.CORRUPT_FILE, "Unrecognised ciphertext")
        dek_kid = blob[11:19].rstrip(b"\0").decode()
        wl = int.from_bytes(blob[19:21], "big")
        nonce = blob[21 + wl:33 + wl]
        _, dek = keyring._dek(tenant, dek_kid)
        return AESGCM(dek).decrypt(nonce, blob[33 + wl:], _aad(object_id, tenant))
    except InvalidTag:
        raise AgentError(ErrorCode.FORBIDDEN, "Decryption failed")
    except (IndexError, ValueError):
        raise AgentError(ErrorCode.CORRUPT_FILE, "Malformed ciphertext")


def blob_nonce(blob: bytes) -> bytes:
    wl = int.from_bytes(blob[19:21], "big")
    return blob[21 + wl:33 + wl]


# --- signatures -----------------------------------------------------------
def sign(payload: bytes) -> dict:
    if keyring.active_signing is None:
        raise AgentError(ErrorCode.ENGINE_FAILED, "No signing key configured")
    kid = keyring.active_signing
    return {"kid": kid, "algorithm": "Ed25519",
            "value": base64.b64encode(keyring.signing[kid].sign(payload)).decode()}


def verify(payload: bytes, sig: dict | None) -> bool:
    try:
        pub = keyring.public.get(sig["kid"])
        if pub is None or sig.get("algorithm") != "Ed25519":
            return False
        pub.verify(base64.b64decode(sig["value"]), payload)
        return True
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


# --- HMAC signed URLs -------------------------------------------------------
def _hm(msg: bytes) -> str:
    if keyring.hmac_key is None:
        raise AgentError(ErrorCode.ENGINE_FAILED, "No HMAC key configured")
    return hmac.new(keyring.hmac_key, msg, hashlib.sha256).hexdigest()


def sign_url(resource: str, ttl_seconds: int, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl_seconds)
    return f"{resource}?exp={exp}&sig={_hm(f'{resource}|{exp}'.encode())}"


def verify_url(resource: str, exp: int, sig: str, now: float | None = None) -> bool:
    if (now if now is not None else time.time()) > exp:
        return False
    return hmac.compare_digest(_hm(f"{resource}|{exp}".encode()), sig)


# --- passwords ------------------------------------------------------------------
def hash_password(password: str) -> str:
    try:
        from argon2 import PasswordHasher  # type: ignore
        return PasswordHasher().hash(password)
    except ImportError:
        salt = os.urandom(16)
        k = Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1).derive(password.encode())
        return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(k).decode()


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("scrypt$"):
        _, s, k = stored.split("$")
        try:
            Scrypt(salt=base64.b64decode(s), length=32, n=2 ** 14, r=8, p=1).verify(password.encode(),
                                                                                    base64.b64decode(k))
            return True
        except (InvalidKey, InvalidSignature):
            return False
    try:
        from argon2 import PasswordHasher  # type: ignore
        return PasswordHasher().verify(stored, password)
    except Exception:  # noqa: BLE001
        return False

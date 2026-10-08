import pytest

from backend.common import crypto
from backend.common.errors import AgentError, ErrorCode


def test_roundtrip():
    blob = crypto.encrypt(b"secret", "obj1")
    assert crypto.decrypt(blob, "obj1") == b"secret"


def test_tamper_one_byte_fails():
    blob = bytearray(crypto.encrypt(b"secret data here", "obj1"))
    blob[-5] ^= 1
    with pytest.raises(AgentError) as e:
        crypto.decrypt(bytes(blob), "obj1")
    assert e.value.code == ErrorCode.FORBIDDEN


def test_wrong_aad_fails():
    blob = crypto.encrypt(b"x", "obj1")
    with pytest.raises(AgentError):
        crypto.decrypt(blob, "obj2")


def test_nonce_unique_10k():
    nonces = {crypto.blob_nonce(crypto.encrypt(b"x", "o")) for _ in range(10000)}
    assert len(nonces) == 10000


def test_signature_modified_payload_fails():
    sig = crypto.sign(b"payload")
    assert crypto.verify(b"payload", sig)
    assert not crypto.verify(b"payload2", sig)


def test_rotated_key_still_verifies_old_signature():
    old = crypto.sign(b"history")
    crypto.keyring.rotate_signing()
    assert crypto.sign(b"new")["kid"] != old["kid"]
    assert crypto.verify(b"history", old)


def test_rotated_kek_still_decrypts_old_data():
    blob = crypto.encrypt(b"old", "o1")
    crypto.keyring.rotate_kek()
    assert crypto.decrypt(blob, "o1") == b"old"


def test_signed_url_expiry_and_tamper():
    url = crypto.sign_url("/exports/a/download", 60, now=1000)
    exp = int(url.split("exp=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    assert crypto.verify_url("/exports/a/download", exp, sig, now=1030)
    assert not crypto.verify_url("/exports/a/download", exp, sig, now=exp + 1)
    assert not crypto.verify_url("/exports/b/download", exp, sig, now=1030)


def test_canonical_json_stable_and_rejects_nan():
    assert crypto.canonical_json({"b": 1, "a": [2, 3]}) == b'{"a":[2,3],"b":1}'
    with pytest.raises(AgentError):
        crypto.canonical_json({"x": float("nan")})


def test_password_hash_roundtrip():
    h = crypto.hash_password("correct horse")
    assert crypto.verify_password("correct horse", h)
    assert not crypto.verify_password("wrong", h)

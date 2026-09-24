"""Security primitives: ULID, token generation, hashing, signing keys, password hashing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt as pyjwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from ulid import ULID

from temanbule.platform.errors import UnauthorizedError
from temanbule.platform.settings import ConfigurationError


def new_ulid() -> str:
    return str(ULID())


def new_opaque_token() -> str:
    """Token opaque 256-bit untuk refresh/action token (hash-only di DB)."""
    return secrets.token_urlsafe(48)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hmac_sha256_hex(secret: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# --- Password hashing (Argon2id) ---


def make_password_hasher(*, memory_kib: int, time_cost: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(
        memory_cost=memory_kib,
        time_cost=time_cost,
        parallelism=parallelism,
    )


def hash_password(hasher: PasswordHasher, password: str) -> str:
    return hasher.hash(password)


def verify_password(hasher: PasswordHasher, password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


# --- Signing keys (RS256) ---


def read_private_key_pem(path: str) -> str:
    content = Path(path).read_text(encoding="utf-8")
    if "PRIVATE KEY" not in content:
        raise ConfigurationError([f"{path} bukan PEM private key"])
    return content


def read_public_key_pem(path: str) -> str:
    content = Path(path).read_text(encoding="utf-8")
    if "PUBLIC KEY" not in content:
        raise ConfigurationError([f"{path} bukan PEM public key"])
    return content


def generate_rsa_keypair(private_path: Path, public_path: Path) -> None:
    """Helper operasional: generate RSA keypair (dipakai script dev)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)


# --- JWT issue/verify ---


def issue_access_jwt(
    *,
    private_key_pem: str,
    algorithm: str,
    key_id: str,
    issuer: str,
    audience: str,
    subject: str,
    family_id: str,
    auth_epoch: int,
    ttl_seconds: int,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "jti": new_ulid(),
        "fam": family_id,
        "epoch": auth_epoch,
    }
    return pyjwt.encode(claims, private_key_pem, algorithm=algorithm, headers={"kid": key_id})


class AccessTokenClaims:
    def __init__(self, *, subject: str, family_id: str, auth_epoch: int, jti: str) -> None:
        self.subject = subject
        self.family_id = family_id
        self.auth_epoch = auth_epoch
        self.jti = jti


def verify_access_jwt(
    *,
    public_key_pem: str,
    algorithm: str,
    issuer: str,
    audience: str,
    token: str,
    clock_skew_seconds: int,
) -> AccessTokenClaims:
    try:
        decoded = pyjwt.decode(
            token,
            public_key_pem,
            algorithms=[algorithm],
            issuer=issuer,
            audience=audience,
            leeway=clock_skew_seconds,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except pyjwt.PyJWTError as exc:
        raise UnauthorizedError("Token tidak valid atau kedaluwarsa.") from exc
    return AccessTokenClaims(
        subject=str(decoded["sub"]),
        family_id=str(decoded.get("fam", "")),
        auth_epoch=int(decoded.get("epoch", 0)),
        jti=str(decoded.get("jti", "")),
    )


def base64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

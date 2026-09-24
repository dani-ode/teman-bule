"""Unit tests untuk security primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from temanbule.platform.errors import UnauthorizedError
from temanbule.platform.security import (
    generate_rsa_keypair,
    hash_password,
    issue_access_jwt,
    make_password_hasher,
    new_opaque_token,
    read_private_key_pem,
    read_public_key_pem,
    sha256_hex,
    verify_access_jwt,
    verify_password,
)


@pytest.fixture()
def keypair(tmp_path: Path):
    priv = tmp_path / "priv.pem"
    pub = tmp_path / "pub.pem"
    generate_rsa_keypair(priv, pub)
    return read_private_key_pem(str(priv)), read_public_key_pem(str(pub))


def test_password_hash_roundtrip():
    hasher = make_password_hasher(memory_kib=1024, time_cost=1, parallelism=1)
    h = hash_password(hasher, "supersecretpassword")
    assert verify_password(hasher, h, "supersecretpassword")
    assert not verify_password(hasher, h, "wrongpassword")


def test_opaque_token_unique():
    assert new_opaque_token() != new_opaque_token()


def test_sha256_hex_deterministic():
    assert sha256_hex("abc") == sha256_hex("abc")
    assert sha256_hex("abc") != sha256_hex("abd")


def test_jwt_issue_and_verify(keypair):
    priv, pub = keypair
    token = issue_access_jwt(
        private_key_pem=priv,
        algorithm="RS256",
        key_id="kid-1",
        issuer="test-iss",
        audience="test-aud",
        subject="user-1",
        family_id="fam-1",
        auth_epoch=3,
        ttl_seconds=60,
    )
    claims = verify_access_jwt(
        public_key_pem=pub,
        algorithm="RS256",
        issuer="test-iss",
        audience="test-aud",
        token=token,
        clock_skew_seconds=5,
    )
    assert claims.subject == "user-1"
    assert claims.family_id == "fam-1"
    assert claims.auth_epoch == 3


def test_jwt_wrong_audience_rejected(keypair):
    priv, pub = keypair
    token = issue_access_jwt(
        private_key_pem=priv,
        algorithm="RS256",
        key_id="kid-1",
        issuer="test-iss",
        audience="test-aud",
        subject="user-1",
        family_id="fam-1",
        auth_epoch=0,
        ttl_seconds=60,
    )
    with pytest.raises(UnauthorizedError):
        verify_access_jwt(
            public_key_pem=pub,
            algorithm="RS256",
            issuer="test-iss",
            audience="other-aud",
            token=token,
            clock_skew_seconds=5,
        )

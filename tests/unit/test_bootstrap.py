"""Unit tests untuk bootstrap manifest (FND-03/DEC-17)."""

from __future__ import annotations

import pytest

from temanbule.modules.reliability.bootstrap import load_manifest
from temanbule.platform.errors import ValidationError


def test_valid_manifest():
    raw = {
        "manifest_version": "1.0.0",
        "environment": "development",
        "entries": [
            {"kind": "plans", "natural_key": {"code": "vip"}, "data": {"policy_version": 1}},
        ],
    }
    manifest = load_manifest(raw)
    assert manifest.manifest_version == "1.0.0"
    assert len(manifest.entries) == 1
    assert len(manifest.checksum()) == 64


def test_manifest_missing_version():
    with pytest.raises(ValidationError):
        load_manifest({"environment": "dev", "entries": []})


def test_manifest_entry_missing_kind():
    with pytest.raises(ValidationError):
        load_manifest(
            {
                "manifest_version": "1",
                "environment": "dev",
                "entries": [{"natural_key": {"code": "x"}}],
            }
        )


def test_manifest_checksum_stable():
    raw = {
        "manifest_version": "1.0.0",
        "environment": "dev",
        "entries": [{"kind": "plans", "natural_key": {"code": "vip"}, "data": {}}],
    }
    assert load_manifest(raw).checksum() == load_manifest(raw).checksum()


def test_manifest_checksum_changes_with_content():
    base = {
        "manifest_version": "1.0.0",
        "environment": "dev",
        "entries": [{"kind": "plans", "natural_key": {"code": "vip"}, "data": {}}],
    }
    modified = {
        "manifest_version": "1.0.0",
        "environment": "dev",
        "entries": [{"kind": "plans", "natural_key": {"code": "advance"}, "data": {}}],
    }
    assert load_manifest(base).checksum() != load_manifest(modified).checksum()

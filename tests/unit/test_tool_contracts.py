"""Tests untuk tool argument validators, contract fixtures, dan catalog parity.

Memastikan:
1. Setiap tool pada katalog tools.v1.json punya validator backend.
2. Fixtures valid diterima validator; fixtures invalid ditolak ValidationError.
3. Authorization matrix backend selaras dengan allowlist blueprint per-purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from temanbule.modules.ai_runtime.tool_schemas import TOOL_VALIDATORS
from temanbule.modules.ai_runtime.tools import (
    PURPOSE_TOOL_ALLOWLIST,
    TOOL_REQUIRED_SCOPE,
)
from temanbule.platform.errors import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog() -> dict:
    return _load("custom_callcraft_spec/tools.v1.json")


@pytest.fixture(scope="module")
def fixtures() -> dict:
    return _load("custom_callcraft_spec/fixtures.v1.json")


def test_every_catalog_tool_has_validator(catalog: dict) -> None:
    catalog_tools = {tool["name"] for tool in catalog["tools"]}
    assert catalog_tools == set(TOOL_VALIDATORS)
    assert catalog_tools == set(TOOL_REQUIRED_SCOPE)


def test_every_catalog_tool_has_fixture(catalog: dict, fixtures: dict) -> None:
    catalog_tools = {tool["name"] for tool in catalog["tools"]}
    fixture_tools = {entry["tool"] for entry in fixtures["fixtures"]}
    assert catalog_tools == fixture_tools


def test_valid_fixtures_pass_validation(fixtures: dict) -> None:
    for entry in fixtures["fixtures"]:
        validator = TOOL_VALIDATORS[entry["tool"]]
        validator(entry["valid_arguments"])  # tidak boleh raise


def test_invalid_fixtures_fail_validation(fixtures: dict) -> None:
    for entry in fixtures["fixtures"]:
        validator = TOOL_VALIDATORS[entry["tool"]]
        for bad in entry["invalid_arguments"]:
            with pytest.raises(ValidationError):
                validator(bad)


def test_actor_user_id_is_rejected_everywhere(catalog: dict) -> None:
    """actor_user_id tidak pernah boleh menjadi argumen tool (blueprint)."""
    sample = {"lemma": "x", "language": "en", "actor_user_id": "01J8VQ2KX3G4Y5Z6A7B8C9D0E1"}
    validator = TOOL_VALIDATORS["vocabulary.save"]
    with pytest.raises(ValidationError):
        validator(sample)


def test_authorization_matrix_matches_catalog_purposes(catalog: dict) -> None:
    """Setiap (tool, purpose) pada katalog harus tercermin di matrix backend."""
    for tool in catalog["tools"]:
        name = tool["name"]
        for purpose in tool["purposes"]:
            assert purpose in PURPOSE_TOOL_ALLOWLIST, f"purpose {purpose} tidak dikenal"
            assert name in PURPOSE_TOOL_ALLOWLIST[purpose], (
                f"{name} seharusnya diizinkan untuk {purpose}"
            )


def test_matrix_does_not_exceed_catalog_purposes(catalog: dict) -> None:
    catalog_purposes = {
        tool["name"]: set(tool["purposes"]) for tool in catalog["tools"]
    }
    for purpose, tools in PURPOSE_TOOL_ALLOWLIST.items():
        for name in tools:
            assert purpose in catalog_purposes[name], (
                f"{name} diizinkan untuk {purpose} di backend tapi tidak pada katalog"
            )


def test_record_progress_has_no_purpose(catalog: dict) -> None:
    """learning.record_progress default deny: tidak ada purpose yang mengizinkan."""
    assert catalog_purposes_empty(catalog, "learning.record_progress")
    assert all(
        "learning.record_progress" not in tools
        for tools in PURPOSE_TOOL_ALLOWLIST.values()
    )


def catalog_purposes_empty(catalog: dict, tool_name: str) -> bool:
    tool = next(t for t in catalog["tools"] if t["name"] == tool_name)
    return not tool["purposes"]

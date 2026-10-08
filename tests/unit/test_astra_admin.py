"""Unit tests untuk AstraAdminClient filter validation/expansion.

Fokus pada _validate_filters (allowlist ketat) dan _expand_filter
(dual-bentuk top-level + metadata.* untuk dokumen writer Langflow).
HTTP layer tidak disentuh — hanya pure static helpers.
"""

from __future__ import annotations

import pytest

from temanbule.modules.knowledge.astra_admin import AstraAdminClient
from temanbule.platform.errors import ValidationError


class TestValidateFilters:
    def test_rejects_empty(self) -> None:
        with pytest.raises(ValidationError, match="Filter kosong"):
            AstraAdminClient._validate_filters({})

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="Filter delete tidak valid"):
            AstraAdminClient._validate_filters({"metadata.document_id": "X"})

    def test_rejects_dot_notation_field(self) -> None:
        # Caller tidak boleh mengirim field dot-notation mentah; ekspansi
        # metadata.* hanya dilakukan internal via _expand_filter.
        with pytest.raises(ValidationError, match="Filter delete tidak valid"):
            AstraAdminClient._validate_filters({"metadata.agent_id": "01ABC"})

    def test_rejects_blank_value(self) -> None:
        with pytest.raises(ValidationError, match="Filter delete tidak valid"):
            AstraAdminClient._validate_filters({"document_id": "   "})

    def test_strips_and_accepts_allowed(self) -> None:
        cleaned = AstraAdminClient._validate_filters(
            {"document_id": "  01M4DKCR33XVMX4WTKHC82AM36  "}
        )
        assert cleaned == {"document_id": "01M4DKCR33XVMX4WTKHC82AM36"}


class TestExpandFilter:
    def test_single_langflow_field_becomes_or(self) -> None:
        expanded = AstraAdminClient._expand_filter({"document_id": "D1"})
        assert expanded == {"$or": [{"document_id": "D1"}, {"metadata.document_id": "D1"}]}

    def test_agent_id_expanded(self) -> None:
        expanded = AstraAdminClient._expand_filter({"agent_id": "A1"})
        assert expanded == {"$or": [{"agent_id": "A1"}, {"metadata.agent_id": "A1"}]}

    def test_non_langflow_field_stays_toplevel(self) -> None:
        expanded = AstraAdminClient._expand_filter({"source_type": "agent_knowledge"})
        assert expanded == {"source_type": "agent_knowledge"}

    def test_multiple_fields_and_composition(self) -> None:
        expanded = AstraAdminClient._expand_filter(
            {"document_id": "D1", "agent_id": "A1"}
        )
        assert expanded == {
            "$and": [
                {"$or": [{"document_id": "D1"}, {"metadata.document_id": "D1"}]},
                {"$or": [{"agent_id": "A1"}, {"metadata.agent_id": "A1"}]},
            ]
        }

    def test_mixed_langflow_and_plain_fields(self) -> None:
        expanded = AstraAdminClient._expand_filter(
            {"document_id": "D1", "projection_generation": "1"}
        )
        assert expanded == {
            "$and": [
                {"$or": [{"document_id": "D1"}, {"metadata.document_id": "D1"}]},
                {"projection_generation": "1"},
            ]
        }

    def test_canonical_chunk_id_expanded(self) -> None:
        expanded = AstraAdminClient._expand_filter({"canonical_chunk_id": "C1"})
        assert expanded == {
            "$or": [{"canonical_chunk_id": "C1"}, {"metadata.canonical_chunk_id": "C1"}]
        }

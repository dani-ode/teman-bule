"""Strict per-tool argument validators aligned with custom_callcraft_spec/tools.v1.json.

Backend adalah gatekeeper otoritatif: field tambahan ditolak
(``additionalProperties: false``), tipe diverifikasi, dan ULID dicek polanya.
Ini melindungi dispatcher dari argumen extraction CallCraft yang menyimpang dari
katalog, tanpa menggantikan validasi domain service.
"""

from __future__ import annotations

import re
from typing import Any, NoReturn

from temanbule.platform.errors import ValidationError

ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
VOCAB_STATES = frozenset({"new", "learning", "review", "mastered"})
FACT_STATUSES = frozenset({"proposed", "confirmed"})
MAX_LESSON_IDS = 50
MAX_MESSAGE_IDS = 200
MAX_CHUNK_IDS = 25
MAX_QUERY_LENGTH = 2000


def _fail(field: str, message: str) -> NoReturn:
    raise ValidationError(
        "Argument tidak valid.", details=[{"field": field, "message": message}]
    )


def _reject_unknown(arguments: dict[str, Any], allowed: set[str]) -> None:
    for key in arguments:
        if key not in allowed:
            _fail(key, "field tidak dikenal")


def _req_str(arguments: dict[str, Any], field: str, *, max_length: int = 4000) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(field, "wajib string non-kosong")
    if len(value) > max_length:
        _fail(field, f"melebihi {max_length} karakter")
    return value

def _opt_str(arguments: dict[str, Any], field: str, *, max_length: int = 4000) -> str | None:
    if field not in arguments or arguments[field] is None:
        return None
    return _req_str(arguments, field, max_length=max_length)


def _req_ulid(arguments: dict[str, Any], field: str) -> str:
    value = _req_str(arguments, field, max_length=26)
    if not ULID_RE.match(value):
        _fail(field, "harus ULID valid")
    return value


def _opt_ulid(arguments: dict[str, Any], field: str) -> str | None:
    if field not in arguments or arguments[field] is None:
        return None
    return _req_ulid(arguments, field)


def _req_int(arguments: dict[str, Any], field: str, *, minimum: int | None = None) -> int:
    value = arguments.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(field, "wajib integer")
    if minimum is not None and value < minimum:
        _fail(field, f"minimal {minimum}")
    return value


def _req_number(
    arguments: dict[str, Any], field: str,
    *, minimum: float | None = None, maximum: float | None = None,
) -> float:
    value = arguments.get(field)
    if not isinstance(value, int | float) or isinstance(value, bool):
        _fail(field, "wajib numerik")
    result = float(value)
    if minimum is not None and result < minimum:
        _fail(field, f"minimal {minimum}")
    if maximum is not None and result > maximum:
        _fail(field, f"maksimal {maximum}")
    return result


def _req_ulid_list(
    arguments: dict[str, Any], field: str, *, max_items: int
) -> list[str]:
    value = arguments.get(field)
    if not isinstance(value, list) or not value:
        _fail(field, "wajib array non-kosong")
    if len(value) > max_items:
        _fail(field, f"maksimal {max_items} item")
    if len(set(value)) != len(value):
        _fail(field, "item duplikat")
    for item in value:
        if not isinstance(item, str) or not ULID_RE.match(item):
            _fail(field, "item harus ULID valid")
    return list(value)


def _req_dimensions(arguments: dict[str, Any], field: str) -> dict[str, float]:
    value = arguments.get(field)
    if not isinstance(value, dict) or not value:
        _fail(field, "wajib object non-kosong")
    out: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            _fail(field, "kunci dimensi wajib string non-kosong")
        if not isinstance(item, int | float) or isinstance(item, bool):
            _fail(field, f"dimensi '{key}' wajib numerik")
        out[key] = float(item)
    return out


# --- Per-tool validators -----------------------------------------------------


def validate_vocabulary_save(arguments: dict[str, Any]) -> None:
    _reject_unknown(arguments, {"lemma", "language", "definition", "example", "source_message_id"})
    _req_str(arguments, "lemma", max_length=200)
    _req_str(arguments, "language", max_length=16)
    _opt_str(arguments, "definition", max_length=2000)
    _opt_str(arguments, "example", max_length=2000)
    _opt_ulid(arguments, "source_message_id")


def validate_vocabulary_update_status(arguments: dict[str, Any]) -> None:
    _reject_unknown(arguments, {"entry_id", "target_state", "expected_version"})
    _req_ulid(arguments, "entry_id")
    state = _req_str(arguments, "target_state", max_length=20)
    if state not in VOCAB_STATES:
        _fail("target_state", "state tidak dikenal")
    _req_int(arguments, "expected_version", minimum=1)


def validate_vocabulary_get(arguments: dict[str, Any]) -> None:
    _reject_unknown(arguments, {"entry_id", "lemma", "language"})
    has_id = "entry_id" in arguments
    has_lemma = "lemma" in arguments or "language" in arguments
    if has_id == has_lemma:
        _fail("entry_id|lemma", "sediakan entry_id ATAU lemma+language")
    if has_id:
        _req_ulid(arguments, "entry_id")
    else:
        _req_str(arguments, "lemma", max_length=200)
        _req_str(arguments, "language", max_length=16)


def validate_profile_update_preferences(arguments: dict[str, Any]) -> None:
    allowed = {"english_level", "learning_goals", "tutoring_preferences"}
    _reject_unknown(arguments, allowed)
    if not arguments:
        _fail("-", "minimal satu preferensi")
    _opt_str(arguments, "english_level", max_length=20)
    goals = arguments.get("learning_goals")
    if goals is not None:
        if not isinstance(goals, list) or not goals:
            _fail("learning_goals", "wajib array non-kosong")
        if len(set(goals)) != len(goals):
            _fail("learning_goals", "item duplikat")
        for goal in goals:
            if not isinstance(goal, str) or not goal.strip():
                _fail("learning_goals", "item wajib string non-kosong")
    prefs = arguments.get("tutoring_preferences")
    if prefs is not None:
        if not isinstance(prefs, dict) or not prefs:
            _fail("tutoring_preferences", "wajib object non-kosong")
        for key, item in prefs.items():
            if not isinstance(item, str | bool):
                _fail("tutoring_preferences", f"nilai '{key}' wajib string/boolean")


def validate_learning_get_progress(arguments: dict[str, Any]) -> None:
    _reject_unknown(arguments, {"lesson_id", "lesson_ids"})
    has_one = "lesson_id" in arguments
    has_many = "lesson_ids" in arguments
    if has_one == has_many:
        _fail("lesson_id|lesson_ids", "sediakan lesson_id ATAU lesson_ids")
    if has_one:
        _req_ulid(arguments, "lesson_id")
    else:
        _req_ulid_list(arguments, "lesson_ids", max_items=MAX_LESSON_IDS)


def validate_learning_record_progress(arguments: dict[str, Any]) -> None:
    _reject_unknown(
        arguments, {"content_version_id", "status", "completion_percent", "expected_version"}
    )
    _req_ulid(arguments, "content_version_id")
    _req_str(arguments, "status", max_length=40)
    _req_number(arguments, "completion_percent", minimum=0, maximum=100)
    _req_int(arguments, "expected_version", minimum=1)


def validate_conversation_persist_extraction(arguments: dict[str, Any]) -> None:
    _reject_unknown(arguments, {"source_message_ids", "extraction"})
    _req_ulid_list(arguments, "source_message_ids", max_items=MAX_MESSAGE_IDS)
    extraction = arguments.get("extraction")
    if not isinstance(extraction, dict):
        _fail("extraction", "wajib object")
    _reject_unknown(extraction, {"schema_version", "summary", "evidence_message_ids"})
    if extraction.get("schema_version") != "1":
        _fail("extraction.schema_version", "harus '1'")
    summary = extraction.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        _fail("extraction.summary", "wajib string non-kosong")
    evidence = extraction.get("evidence_message_ids")
    if not isinstance(evidence, list) or not evidence:
        _fail("extraction.evidence_message_ids", "wajib array non-kosong")
    for item in evidence:
        if not isinstance(item, str) or not ULID_RE.match(item):
            _fail("extraction.evidence_message_ids", "item harus ULID valid")


def validate_toefl_record_evaluation(arguments: dict[str, Any]) -> None:
    _reject_unknown(
        arguments,
        {"attempt_id", "rubric_version", "dimensions", "total_score", "feedback",
         "evaluator_flow_version"},
    )
    _req_ulid(arguments, "attempt_id")
    _req_str(arguments, "rubric_version", max_length=80)
    _req_dimensions(arguments, "dimensions")
    _req_number(arguments, "total_score", minimum=0)
    _req_str(arguments, "feedback", max_length=8000)
    _req_str(arguments, "evaluator_flow_version", max_length=80)


def validate_user_facts_upsert(arguments: dict[str, Any]) -> None:
    _reject_unknown(
        arguments,
        {"fact_key", "value", "confidence", "source_message_ids", "expected_version",
         "proposed_status"},
    )
    _req_str(arguments, "fact_key", max_length=128)
    value = arguments.get("value")
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            _fail("value", "array value wajib string")
    elif not isinstance(value, str | int | float | bool):
        _fail("value", "wajib string/number/boolean/array-of-string")
    _req_number(arguments, "confidence", minimum=0, maximum=1)
    _req_ulid_list(arguments, "source_message_ids", max_items=MAX_MESSAGE_IDS)
    expected = arguments.get("expected_version")
    if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool)
                                 or expected < 1):
        _fail("expected_version", "wajib null atau integer >= 1")
    status = arguments.get("proposed_status")
    if status not in FACT_STATUSES:
        _fail("proposed_status", "harus proposed|confirmed")


def validate_learning_record_assessment(arguments: dict[str, Any]) -> None:
    _reject_unknown(
        arguments,
        {"session_id", "source_range", "rubric_version", "dimensions",
         "evidence_message_ids", "suggested_level"},
    )
    _req_ulid(arguments, "session_id")
    source_range = arguments.get("source_range")
    if not isinstance(source_range, dict):
        _fail("source_range", "wajib object {start, end}")
    _reject_unknown(source_range, {"start", "end"})
    start = source_range.get("start")
    end = source_range.get("end")
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        _fail("source_range.start", "wajib integer >= 0")
    if not isinstance(end, int) or isinstance(end, bool) or end < 0:
        _fail("source_range.end", "wajib integer >= 0")
    _req_str(arguments, "rubric_version", max_length=80)
    _req_dimensions(arguments, "dimensions")
    _req_ulid_list(arguments, "evidence_message_ids", max_items=MAX_MESSAGE_IDS)
    _req_str(arguments, "suggested_level", max_length=20)


def validate_podcast_get_source_context(arguments: dict[str, Any]) -> None:
    _reject_unknown(arguments, {"podcast_id", "source_version_id", "chunk_ids", "query"})
    _req_ulid(arguments, "podcast_id")
    _req_ulid(arguments, "source_version_id")
    has_chunks = "chunk_ids" in arguments
    has_query = "query" in arguments
    if has_chunks == has_query:
        _fail("chunk_ids|query", "sediakan chunk_ids ATAU query (salah satu)")
    if has_chunks:
        _req_ulid_list(arguments, "chunk_ids", max_items=MAX_CHUNK_IDS)
    else:
        _req_str(arguments, "query", max_length=MAX_QUERY_LENGTH)


TOOL_VALIDATORS = {
    "vocabulary.save": validate_vocabulary_save,
    "vocabulary.update_status": validate_vocabulary_update_status,
    "vocabulary.get": validate_vocabulary_get,
    "profile.update_preferences": validate_profile_update_preferences,
    "learning.get_progress": validate_learning_get_progress,
    "learning.record_progress": validate_learning_record_progress,
    "conversation.persist_extraction": validate_conversation_persist_extraction,
    "toefl.record_evaluation": validate_toefl_record_evaluation,
    "user_facts.upsert": validate_user_facts_upsert,
    "learning.record_assessment": validate_learning_record_assessment,
    "podcast.get_source_context": validate_podcast_get_source_context,
}

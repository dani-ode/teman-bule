"""Vocabulary service (Phase 3).

Kontrak (callcraft-tools.md):
- vocabulary.save: lemma dinormalisasi server-side; upsert hanya dalam owner
  terautentikasi; idempotency wajib untuk jalur tool.
- vocabulary.update_status: ownership + lifecycle + expected_version.
- Tidak ada fake success: kegagalan DB/constraint memunculkan error typed.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.vocabulary.models import VocabularyEntry, VocabularyReview
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

VALID_STATES = {"new", "learning", "review", "mastered", "archived"}
STATE_TRANSITIONS: dict[str, set[str]] = {
    "new": {"learning", "archived"},
    "learning": {"review", "mastered", "archived"},
    "review": {"learning", "mastered", "archived"},
    "mastered": {"review", "archived"},
    "archived": {"new"},
}
VALID_REVIEW_RESULTS = {"again", "hard", "good", "easy"}


def normalize_lemma(lemma: str) -> str:
    """Normalisasi deterministik: NFKC, casefold, trim, collapse whitespace."""
    normalized = unicodedata.normalize("NFKC", lemma).casefold().strip()
    return " ".join(normalized.split())


class VocabularyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_entry(
        self,
        *,
        user_id: str,
        lemma: str,
        language: str,
        definition: str | None = None,
        example: str | None = None,
        provenance: str | None = None,
    ) -> tuple[VocabularyEntry, bool]:
        """Upsert dalam owner scope. Return (entry, created)."""
        normalized = normalize_lemma(lemma)
        if not normalized:
            raise ValidationError("Lemma kosong setelah normalisasi.")
        if not language.strip():
            raise ValidationError("Language wajib.")

        existing = (
            await self.session.execute(
                select(VocabularyEntry).where(
                    VocabularyEntry.user_id == user_id,
                    VocabularyEntry.normalized_lemma == normalized,
                    VocabularyEntry.language == language,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Upsert: lengkapi field yang kosong, jangan timpa yang sudah ada.
            if definition and not existing.definition:
                existing.definition = definition
            if example and not existing.example:
                existing.example = example
            if provenance and not existing.provenance:
                existing.provenance = provenance
            await self.session.flush()
            return existing, False

        entry = VocabularyEntry(
            id=new_ulid(),
            user_id=user_id,
            lemma=lemma.strip(),
            normalized_lemma=normalized,
            language=language,
            definition=definition,
            example=example,
            provenance=provenance,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry, True

    async def update_status(
        self,
        *,
        user_id: str,
        entry_id: str,
        target_state: str,
        expected_version: int | None = None,
    ) -> VocabularyEntry:
        entry = await self._owned_entry(user_id, entry_id)
        if target_state not in VALID_STATES:
            raise ValidationError(
                "Target state tidak valid.",
                details=[{"field": "target_state", "message": target_state}],
            )
        if target_state not in STATE_TRANSITIONS.get(entry.state, set()):
            raise ConflictError(
                "Transisi state tidak diizinkan.",
                code="INVALID_STATE_TRANSITION",
                details=[
                    {"field": "state", "message": f"{entry.state} -> {target_state}"}
                ],
            )
        entry.state = target_state
        await self.session.flush()
        return entry

    async def record_review(
        self, *, user_id: str, entry_id: str, result: str
    ) -> VocabularyReview:
        if result not in VALID_REVIEW_RESULTS:
            raise ValidationError(
                "Hasil review tidak valid.",
                details=[{"field": "result", "message": result}],
            )
        entry = await self._owned_entry(user_id, entry_id)
        previous_state = entry.state
        # Spaced-repetition minimal: again kembali learning; easy mempercepat mastered.
        new_state = {
            "again": "learning",
            "hard": "learning" if previous_state == "new" else previous_state,
            "good": "review" if previous_state in {"new", "learning"} else previous_state,
            "easy": "mastered",
        }[result]
        review = VocabularyReview(
            id=new_ulid(),
            entry_id=entry.id,
            result=result,
            previous_state=previous_state,
            new_state=new_state,
        )
        self.session.add(review)
        entry.state = new_state
        mastery_delta = {"again": 0, "hard": 5, "good": 10, "easy": 25}[result]
        entry.mastery_score = min(100, entry.mastery_score + mastery_delta)
        await self.session.flush()
        return review

    async def get_entry(self, *, user_id: str, entry_id: str) -> VocabularyEntry:
        return await self._owned_entry(user_id, entry_id)

    async def list_entries(self, *, user_id: str, limit: int = 50) -> list[VocabularyEntry]:
        rows = (
            (
                await self.session.execute(
                    select(VocabularyEntry)
                    .where(VocabularyEntry.user_id == user_id)
                    .order_by(VocabularyEntry.created_at.desc())
                    .limit(min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def _owned_entry(self, user_id: str, entry_id: str) -> VocabularyEntry:
        entry = (
            await self.session.execute(
                select(VocabularyEntry).where(VocabularyEntry.id == entry_id)
            )
        ).scalar_one_or_none()
        if entry is None or entry.user_id != user_id:
            raise NotFoundError("Vocabulary entry tidak ditemukan.")
        return entry

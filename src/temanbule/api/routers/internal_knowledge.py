"""Internal knowledge router: admin delete untuk Astra vector projections.

Astra DB dashboard tidak menyediakan hapus baris, sehingga pembersihan
projection (user_memory, agent_knowledge, podcast) dilakukan lewat endpoint
internal ini. Bukan API publik: caller wajib M2M service token
(``X-Service-Token``); tidak ada user JWT.

Aturan keamanan:
- Scope dibatasi ke tiga koleksi logis: user_memory, agent_knowledge, podcast.
  Nama koleksi fisik Astra di-resolve dari vector_collection_registry SQL
  (environment settings.app_env, status active) — caller tidak pernah
  mengirim nama koleksi fisik.
- Tiap scope punya field filter wajib minimal satu:
  user_memory     → owner_user_id | document_id
  agent_knowledge → agent_id | document_id
  podcast         → podcast_id | document_id
- Filter opsional tambahan (source_version, embedding_profile_id, dst.)
  divalidasi ketat oleh AstraAdminClient. Delete tanpa filter ditolak.
- Koleksi bisa berisi campuran dua bentuk dokumen: writer backend (field
  top-level) dan writer Langflow (custom field di bawah objek ``metadata``).
  AstraAdminClient._expand_filter memperluas tiap field kunci ke klausa
  ``$or`` (top-level + metadata.<field>) sehingga delete match keduanya.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from temanbule.api.deps import SessionDep, SettingsDep, require_service_identity
from temanbule.modules.knowledge.astra_admin import (
    AstraAdminClient,
    build_astra_admin_client,
)
from temanbule.modules.knowledge.models import VectorCollectionRegistry
from temanbule.platform.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/internal/v1/knowledge", tags=["internal-knowledge"])

ServiceIdentity = Annotated[str, Depends(require_service_identity)]

SCOPE_USER_MEMORY = "user_memory"
SCOPE_AGENT_KNOWLEDGE = "agent_knowledge"
SCOPE_PODCAST = "podcast"

_ULID_PATTERN = r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"


class UserMemoryDeleteRequest(BaseModel):
    owner_user_id: str | None = Field(default=None, pattern=_ULID_PATTERN)
    document_id: str | None = Field(default=None, pattern=_ULID_PATTERN)
    source_version: str | None = Field(default=None, min_length=1, max_length=40)
    embedding_profile_id: str | None = Field(default=None, pattern=_ULID_PATTERN)


class AgentKnowledgeDeleteRequest(BaseModel):
    agent_id: str | None = Field(default=None, pattern=_ULID_PATTERN)
    document_id: str | None = Field(default=None, pattern=_ULID_PATTERN)
    source_version: str | None = Field(default=None, min_length=1, max_length=40)
    embedding_profile_id: str | None = Field(default=None, pattern=_ULID_PATTERN)


class PodcastDeleteRequest(BaseModel):
    podcast_id: str | None = Field(default=None, pattern=_ULID_PATTERN)
    document_id: str | None = Field(default=None, pattern=_ULID_PATTERN)
    source_version: str | None = Field(default=None, min_length=1, max_length=40)
    embedding_profile_id: str | None = Field(default=None, pattern=_ULID_PATTERN)


class DeleteResponse(BaseModel):
    scope: str
    collection: str
    deleted_count: int


async def _resolve_collections(
    session: SessionDep,
    *,
    environment: str,
    scope: str,
) -> list[str]:
    """Semua physical collection aktif untuk scope pada environment ini.

    Satu scope bisa punya beberapa binding aktif (multi embedding profile);
    delete diterapkan ke semuanya agar tidak ada projection yatim.
    """
    rows = (
        (
            await session.execute(
                select(VectorCollectionRegistry.physical_name).where(
                    VectorCollectionRegistry.environment == environment,
                    VectorCollectionRegistry.scope == scope,
                    VectorCollectionRegistry.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise NotFoundError(
            f"Tidak ada koleksi aktif untuk scope {scope} pada environment ini."
        )
    return list(rows)


def _filters_from(body: BaseModel, required_any: set[str]) -> dict[str, str]:
    """Kumpulkan field terisi; wajibkan minimal satu field kunci kepemilikan."""
    filters = {
        field: value
        for field, value in body.model_dump().items()
        if value is not None and str(value).strip()
    }
    if not any(key in filters for key in required_any):
        raise ValidationError(
            "Minimal satu field kunci wajib diisi: " + ", ".join(sorted(required_any))
        )
    return filters


async def _delete_scope(
    *,
    session: SessionDep,
    settings: SettingsDep,
    scope: str,
    filters: dict[str, str],
) -> DeleteResponse:
    collections = await _resolve_collections(
        session, environment=settings.app_env, scope=scope
    )
    client: AstraAdminClient = build_astra_admin_client(settings)
    total_deleted = 0
    for collection in collections:
        total_deleted += await client.delete_many(
            collection=collection, filters=filters
        )
    return DeleteResponse(
        scope=scope,
        collection=",".join(collections),
        deleted_count=total_deleted,
    )


@router.delete("/user-memory/projections", response_model=DeleteResponse)
async def delete_user_memory_projections(
    body: UserMemoryDeleteRequest,
    session: SessionDep,
    settings: SettingsDep,
    service_identity: ServiceIdentity,
) -> DeleteResponse:
    """Hapus projection user_memory by owner_user_id dan/atau document_id."""
    del service_identity
    return await _delete_scope(
        session=session,
        settings=settings,
        scope=SCOPE_USER_MEMORY,
        filters=_filters_from(body, {"owner_user_id", "document_id"}),
    )


@router.delete("/agent-knowledge/projections", response_model=DeleteResponse)
async def delete_agent_knowledge_projections(
    body: AgentKnowledgeDeleteRequest,
    session: SessionDep,
    settings: SettingsDep,
    service_identity: ServiceIdentity,
) -> DeleteResponse:
    """Hapus projection agent_knowledge by agent_id dan/atau document_id."""
    del service_identity
    return await _delete_scope(
        session=session,
        settings=settings,
        scope=SCOPE_AGENT_KNOWLEDGE,
        filters=_filters_from(body, {"agent_id", "document_id"}),
    )


@router.delete("/podcast/projections", response_model=DeleteResponse)
async def delete_podcast_projections(
    body: PodcastDeleteRequest,
    session: SessionDep,
    settings: SettingsDep,
    service_identity: ServiceIdentity,
) -> DeleteResponse:
    """Hapus projection podcast by podcast_id dan/atau document_id."""
    del service_identity
    return await _delete_scope(
        session=session,
        settings=settings,
        scope=SCOPE_PODCAST,
        filters=_filters_from(body, {"podcast_id", "document_id"}),
    )

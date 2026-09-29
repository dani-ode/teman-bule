"""Catalog-owned, secret-free context for chat workflows."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.models import Agent, AgentVersion, RuntimeSnapshot
from temanbule.platform.errors import DependencyUnavailableError, NotFoundError


async def chat_context(session: AsyncSession, snapshot_id: str, user_id: str) -> dict[str, Any]:
    snapshot = await session.get(RuntimeSnapshot, snapshot_id)
    if snapshot is None or snapshot.owner_user_id != user_id:
        raise NotFoundError("Runtime snapshot tidak ditemukan.")
    persona = (await session.execute(
        select(Agent.code).join(AgentVersion, AgentVersion.agent_id == Agent.id).where(
            AgentVersion.id == snapshot.agent_version_id,
            AgentVersion.status == "published", Agent.status == "active",
        )
    )).scalar_one_or_none()
    if persona not in {"elean", "willy"}:
        raise DependencyUnavailableError("Persona snapshot tidak tersedia.")
    return {
        "persona": persona,
        "agent_version_id": snapshot.agent_version_id,
        "runtime_snapshot_id": snapshot.id,
        "ai_configuration": {
            "model_configuration_id": snapshot.llm_model_configuration_id,
            "capability": "llm",
        },
        "policy": json.loads(snapshot.policy),
    }

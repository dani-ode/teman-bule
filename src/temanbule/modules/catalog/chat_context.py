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
    agent_row = (await session.execute(
        select(Agent.id, Agent.code, Agent.display_name)
        .join(AgentVersion, AgentVersion.agent_id == Agent.id)
        .where(
            AgentVersion.id == snapshot.agent_version_id,
            AgentVersion.status == "published", Agent.status == "active",
        )
    )).one_or_none()
    if agent_row is None or agent_row.code not in {"elean", "willy"}:
        raise DependencyUnavailableError("Persona snapshot tidak tersedia.")
    return {
        "persona": agent_row.display_name,
        "agent_code": agent_row.code,
        "agent_version_id": snapshot.agent_version_id,
        "runtime_snapshot_id": snapshot.id,
        "ai_configuration": agent_row.id,
        "policy": json.loads(snapshot.policy),
    }

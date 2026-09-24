"""AI runtime repository: execution grants."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.ai_runtime.models import ExecutionGrant


class AiRuntimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add_grant(self, grant: ExecutionGrant) -> None:
        self.session.add(grant)

    async def get_grant(self, grant_id: str) -> ExecutionGrant | None:
        return await self.session.get(ExecutionGrant, grant_id)

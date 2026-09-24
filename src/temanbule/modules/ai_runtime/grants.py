"""Execution grant service (FND-09): issue/validate grant untuk internal runtime."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.ai_runtime.models import ExecutionGrant
from temanbule.modules.ai_runtime.repository import AiRuntimeRepository
from temanbule.platform.errors import ForbiddenError, UnauthorizedError
from temanbule.platform.security import new_ulid


class ExecutionGrantService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AiRuntimeRepository(session)

    def issue_grant(
        self,
        *,
        request_id: str,
        service_identity: str,
        purpose: str,
        scopes: list[str],
        ttl_seconds: int,
        owner_user_id: str | None = None,
        resource_ref: str | None = None,
        snapshot_id: str | None = None,
        replay_policy: str = "single_use",
    ) -> ExecutionGrant:
        now = datetime.now(UTC)
        grant = ExecutionGrant(
            id=new_ulid(),
            request_id=request_id,
            snapshot_id=snapshot_id,
            service_identity=service_identity,
            owner_user_id=owner_user_id,
            resource_ref=resource_ref,
            purpose=purpose,
            scopes=json.dumps(scopes),
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            deadline_at=now + timedelta(seconds=ttl_seconds * 2),
            replay_policy=replay_policy,
        )
        self.repo.add_grant(grant)
        return grant

    async def validate_grant(
        self,
        *,
        grant_id: str,
        expected_service_identity: str,
        required_scope: str | None = None,
        consume: bool = True,
    ) -> ExecutionGrant:
        grant = await self.repo.get_grant(grant_id)
        if grant is None:
            raise UnauthorizedError("Execution grant tidak ditemukan.", code="GRANT_NOT_FOUND")
        now = datetime.now(UTC)
        if grant.revoked_at is not None:
            raise ForbiddenError("Execution grant telah dicabut.", code="GRANT_REVOKED")
        if grant.expires_at <= now:
            raise UnauthorizedError("Execution grant kedaluwarsa.", code="GRANT_EXPIRED")
        if grant.service_identity != expected_service_identity:
            raise ForbiddenError("Service identity tidak cocok.", code="GRANT_WRONG_SERVICE")
        if required_scope is not None:
            scopes: list[str] = json.loads(grant.scopes)
            if required_scope not in scopes:
                raise ForbiddenError("Scope tidak diizinkan.", code="GRANT_SCOPE_DENIED")
        if consume:
            if grant.replay_policy == "single_use" and grant.consumed_at is not None:
                raise ForbiddenError("Execution grant sudah dikonsumsi.", code="GRANT_REPLAY")
            grant.consumed_at = now
        return grant

    async def revoke_grant(self, grant_id: str) -> None:
        grant = await self.repo.get_grant(grant_id)
        if grant is not None and grant.revoked_at is None:
            grant.revoked_at = datetime.now(UTC)

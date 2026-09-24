"""Tool execution service (Phase 3): jalur /internal/v1/tools/* via CallCraft.

Kontrak (callcraft-tools.md, runtime-components.md):
- actor_user_id TIDAK pernah diterima sebagai argumen bebas; owner berasal
  dari execution grant terverifikasi.
- Mutasi wajib idempotency_key; dedupe via tool_executions UNIQUE
  (user, tool, key); key sama + payload berbeda → 409.
- Scope tool divalidasi dari grant; authorization matrix per purpose.
- Tidak ada fake success: domain error mengembalikan status failed dengan
  stable code, bukan klaim sukses.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.ai_runtime.grants import ExecutionGrantService
from temanbule.modules.ai_runtime.models import ExecutionGrant
from temanbule.modules.reliability.models import ToolExecution
from temanbule.platform.errors import (
    AppError,
    ConflictError,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError,
)
from temanbule.platform.security import new_ulid, sha256_hex

# Authorization matrix (callcraft-tools.md): purpose → tool allowlist
PURPOSE_TOOL_ALLOWLIST: dict[str, set[str]] = {
    "practice_interaction": {
        "vocabulary.save",
        "vocabulary.update_status",
        "vocabulary.get",
        "profile.update_preferences",
    },
    "conversation_ingestion": {"conversation.persist_extraction"},
    "user_fact_extraction": {"user_facts.upsert"},
    "learning_assessment": {"learning.record_assessment"},
    "learning_assistance": {"learning.get_progress"},
    "toefl_evaluation": {"toefl.record_evaluation"},
}

# Tool → scope wajib pada grant
TOOL_REQUIRED_SCOPE: dict[str, str] = {
    "vocabulary.save": "vocabulary:write",
    "vocabulary.update_status": "vocabulary:write",
    "vocabulary.get": "vocabulary:read",
    "profile.update_preferences": "profile:write",
    "learning.get_progress": "learning:read",
    "learning.record_progress": "learning:write",
    "conversation.persist_extraction": "ingestion:write",
    "toefl.record_evaluation": "toefl:evaluate",
    "user_facts.upsert": "facts:write",
    "learning.record_assessment": "assessment:write",
    "podcast.get_source_context": "podcast:read",
}

ToolHandler = Callable[[AsyncSession, str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolResult:
    execution_id: str
    status: str  # succeeded | failed
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    replayed: bool


class ToolExecutionService:
    """Dispatcher internal tools dengan grant + idempotency + audit trail."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.grants = ExecutionGrantService(session)
        self._handlers: dict[str, ToolHandler] = {}

    def register_handler(self, tool_name: str, handler: ToolHandler) -> None:
        if tool_name not in TOOL_REQUIRED_SCOPE:
            raise ValueError(f"Tool tidak dikenal: {tool_name}")
        self._handlers[tool_name] = handler

    async def execute(
        self,
        *,
        tool_name: str,
        grant_id: str,
        service_identity: str,
        request_id: str,
        idempotency_key: str | None,
        arguments: dict[str, Any],
    ) -> ToolResult:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise NotFoundError(f"Tool '{tool_name}' tidak terdaftar.")

        required_scope = TOOL_REQUIRED_SCOPE[tool_name]
        grant = await self.grants.validate_grant(
            grant_id=grant_id,
            expected_service_identity=service_identity,
            required_scope=required_scope,
            consume=False,  # grant multi-call dalam request yang sama diizinkan
        )
        self._assert_purpose_allowlist(grant, tool_name)

        if grant.owner_user_id is None:
            raise ValidationError("Tool user-scoped memerlukan owner pada grant.")
        owner_user_id = grant.owner_user_id

        # Mutasi WAJIB idempotency key (callcraft-tools.md); read tidak.
        is_read_tool = tool_name in {
            "vocabulary.get",
            "learning.get_progress",
            "podcast.get_source_context",
        }
        if not is_read_tool and idempotency_key is None:
            raise ValidationError(
                "Tool mutasi wajib idempotency_key.",
                details=[{"field": "idempotency_key", "message": "required"}],
            )
        is_mutation = idempotency_key is not None
        request_hash = sha256_hex(
            json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True)
        )
        if is_mutation:
            assert idempotency_key is not None  # noqa: S101 - dijamin guard di atas
            replay = await self._dedupe(
                owner_user_id, tool_name, idempotency_key, request_hash
            )
            if replay is not None:
                return replay

        try:
            result = await handler(self.session, owner_user_id, arguments)
        except AppError as exc:
            if is_mutation:
                assert idempotency_key is not None  # noqa: S101
                await self._record_execution(
                    owner_user_id, tool_name, idempotency_key, request_hash,
                    status="failed", result_ref=None, error=exc.code,
                )
            return ToolResult(
                execution_id=new_ulid(),
                status="failed",
                result=None,
                error_code=exc.code,
                error_message=exc.message,
                replayed=False,
            )

        if is_mutation:
            assert idempotency_key is not None  # noqa: S101
            await self._record_execution(
                owner_user_id, tool_name, idempotency_key, request_hash,
                status="succeeded",
                result_ref=json.dumps(result, sort_keys=True),
                error=None,
            )
        return ToolResult(
            execution_id=new_ulid(),
            status="succeeded",
            result=result,
            error_code=None,
            error_message=None,
            replayed=False,
        )

    def _assert_purpose_allowlist(self, grant: ExecutionGrant, tool_name: str) -> None:
        allowlist = PURPOSE_TOOL_ALLOWLIST.get(grant.purpose, set())
        if tool_name not in allowlist:
            raise ConflictError(
                f"Tool '{tool_name}' tidak diizinkan untuk purpose '{grant.purpose}'.",
                code="TOOL_PURPOSE_DENIED",
            )

    async def _dedupe(
        self, user_id: str, tool_name: str, idempotency_key: str, request_hash: str
    ) -> ToolResult | None:
        existing = (
            await self.session.execute(
                select(ToolExecution).where(
                    ToolExecution.user_id == user_id,
                    ToolExecution.tool_name == tool_name,
                    ToolExecution.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency key sama dengan payload berbeda.",
            )
        return ToolResult(
            execution_id=existing.id,
            status=existing.status,
            result=json.loads(existing.result_ref) if existing.result_ref else None,
            error_code=None if existing.status == "succeeded" else "PREVIOUSLY_FAILED",
            error_message=None,
            replayed=True,
        )

    async def _record_execution(
        self,
        user_id: str,
        tool_name: str,
        idempotency_key: str,
        request_hash: str,
        *,
        status: str,
        result_ref: str | None,
        error: str | None,
    ) -> None:
        del error  # detail error tidak disimpan; stable code sudah di response
        execution = ToolExecution(
            id=new_ulid(),
            user_id=user_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result_ref=result_ref,
            status=status,
        )
        self.session.add(execution)
        await self.session.flush()

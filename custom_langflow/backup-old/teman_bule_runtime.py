"""Trusted runtime boundary shared by Teman Bule Langflow components.

The backend endpoints are proposed contracts, not implemented vendor APIs.
No provider credential is resolved or returned through the Langflow canvas.
"""

import json
import os
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, ValidationError

Identifier = Annotated[str, Field(pattern=r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")]
Nonempty = Annotated[str, Field(min_length=1)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RuntimeRequest(ContractModel):
    schema_version: Literal["1"]
    request_id: Identifier
    execution_ref: Identifier


class RuntimeContext(ContractModel):
    schema_version: Literal["1"]
    request_id: Identifier
    execution_ref: Identifier
    runtime_snapshot_id: Identifier
    purpose: Nonempty
    plan: Literal["vip", "advance"]
    capability: Literal["llm", "stt", "embedding", "tts", "background"]
    payer: Literal["vip_wallet", "user_byok", "platform"]
    model_configuration_id: Identifier
    allowed_tools: list[Nonempty]


class ToolRequest(RuntimeRequest):
    tool_name: Nonempty
    arguments: dict[str, JsonValue]
    idempotency_key: Identifier | None = None


class ToolError(ContractModel):
    code: Literal[
        "INVALID_ARGUMENT", "UNAUTHENTICATED", "FORBIDDEN", "RESOURCE_NOT_FOUND",
        "STATE_CONFLICT", "IDEMPOTENCY_CONFLICT", "RATE_LIMITED",
        "DEPENDENCY_UNAVAILABLE", "INTERNAL",
    ]
    message: Nonempty
    request_id: Identifier


class ToolResponse(ContractModel):
    schema_version: Literal["1"]
    execution_id: Identifier
    status: Literal["succeeded", "failed"]
    result: dict[str, JsonValue] | None
    error: ToolError | None


class RuntimeBoundaryError(RuntimeError):
    """Sanitized boundary failure; remote bodies and secrets are never included."""


class RuntimeSettings(ContractModel):
    base_url: Nonempty
    service_token: SecretStr
    context_path: Nonempty
    tool_path: Nonempty
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_response_bytes: Annotated[int, Field(gt=0)]

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        try:
            settings = cls(
                base_url=os.environ["TEMAN_BULE_RUNTIME_URL"],
                service_token=SecretStr(os.environ["TEMAN_BULE_RUNTIME_SERVICE_TOKEN"]),
                context_path=os.environ["TEMAN_BULE_RUNTIME_CONTEXT_PATH"],
                tool_path=os.environ["TEMAN_BULE_RUNTIME_TOOL_PATH"],
                timeout_seconds=float(os.environ["TEMAN_BULE_RUNTIME_TIMEOUT_SECONDS"]),
                max_response_bytes=int(os.environ["TEMAN_BULE_RUNTIME_MAX_RESPONSE_BYTES"]),
            )
            settings.validate_transport()
            return settings
        except (KeyError, ValueError):
            raise RuntimeBoundaryError("Invalid or missing runtime service configuration") from None

    def validate_transport(self) -> None:
        url = urlsplit(self.base_url)
        if (
            url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ("", "/")
        ):
            raise RuntimeBoundaryError("Runtime URL must be an HTTPS origin")
        if not self.service_token.get_secret_value().strip():
            raise RuntimeBoundaryError("Runtime service token is required")
        for path in (self.context_path, self.tool_path):
            if not path.startswith("/") or path.startswith("//") or any(
                character in path for character in ("?", "#", "\\", "%")
            ) or ".." in path:
                raise RuntimeBoundaryError("Runtime endpoint must be an absolute local path")


class RuntimeClient:
    def __init__(
        self, settings: RuntimeSettings, transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings.validate_transport()
        self.settings = settings
        self.transport = transport

    async def _post(self, path: str, request: RuntimeRequest) -> JsonValue:
        settings = self.settings
        headers = {
            "Authorization": f"Bearer {settings.service_token.get_secret_value()}",
            "X-Request-ID": request.request_id,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.timeout_seconds, follow_redirects=False,
                trust_env=False, transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST", settings.base_url.rstrip("/") + path,
                    headers=headers, json=request.model_dump(exclude_none=True),
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeBoundaryError(
                            f"Runtime service rejected request (HTTP {response.status_code})"
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > settings.max_response_bytes:
                            raise RuntimeBoundaryError("Runtime response exceeds configured limit")
                    return json.loads(body)  # type: ignore[no-any-return]
        except httpx.HTTPError:
            raise RuntimeBoundaryError("Runtime transport failed; outcome may be unknown") from None
        except (ValueError, UnicodeError):
            raise RuntimeBoundaryError("Runtime returned invalid JSON") from None

    async def resolve_context(self, request: RuntimeRequest) -> RuntimeContext:
        payload = await self._post(self.settings.context_path, request)
        try:
            context = RuntimeContext.model_validate(payload)
        except ValidationError:
            raise RuntimeBoundaryError("Invalid runtime context response") from None
        if (context.request_id, context.execution_ref) != (
            request.request_id, request.execution_ref,
        ):
            raise RuntimeBoundaryError("Runtime context correlation mismatch")
        return context

    async def execute_tool(self, request: ToolRequest) -> ToolResponse:
        # Backend reauthorizes and validates catalog schemas on every invocation.
        # Never trust an editable canvas context as proof of authorization.
        payload = await self._post(self.settings.tool_path, request)
        try:
            response = ToolResponse.model_validate(payload)
        except ValidationError:
            raise RuntimeBoundaryError("Invalid tool response") from None
        if response.status == "succeeded":
            if response.result is None or response.error is not None:
                raise RuntimeBoundaryError("Inconsistent successful tool response")
        elif response.result is not None or response.error is None:
            raise RuntimeBoundaryError("Inconsistent failed tool response")
        elif response.error.request_id != request.request_id:
            raise RuntimeBoundaryError("Tool error correlation mismatch")
        return response

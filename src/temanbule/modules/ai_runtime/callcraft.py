"""Trusted server-side CallCraft HTTP client.

This adapter is deliberately separate from the Langflow canvas component. It
never accepts credentials, project IDs, or endpoint URLs from model-visible
arguments. A transport failure is reported as an unknown outcome so callers
must reconcile mutations with the same idempotency key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from temanbule.platform.errors import DependencyUnavailableError


@dataclass(frozen=True)
class CallCraftConfig:
    base_url: str
    user_id: str
    public_key: str
    auth_key: str
    project_id: str
    timeout_seconds: float = 15.0

    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/call"


@dataclass(frozen=True)
class CallCraftOutcome:
    status_code: int
    body: dict[str, Any]
    outcome_unknown: bool = False


class CallCraftClient:
    def __init__(
        self,
        config: CallCraftConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.base_url.startswith("https://"):
            raise ValueError("CallCraft base URL must use HTTPS")
        self.config = config
        self.transport = transport

    async def execute(
        self,
        *,
        spec_id: str,
        arguments: dict[str, Any],
        request_id: str,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> CallCraftOutcome:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-USER-ID": self.config.user_id,
            "X-CALL-PUBLIC-KEY": self.config.public_key,
            "Authorization": f"Bearer {self.config.auth_key}",
            "X-PROJECT-ID": self.config.project_id,
            "X-CALL-SPEC-ID": spec_id,
            "X-Request-ID": request_id,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        payload = {"arguments": arguments}
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds or self.config.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    self.config.endpoint(), headers=headers, json=payload
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DependencyUnavailableError(
                "CallCraft request outcome is unknown; reconcile before retrying.",
                code="CALLCRAFT_OUTCOME_UNKNOWN",
            ) from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                "CallCraft transport failed.", code="CALLCRAFT_TRANSPORT_FAILED"
            ) from exc

        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise DependencyUnavailableError(
                "CallCraft returned invalid JSON.", code="CALLCRAFT_INVALID_RESPONSE"
            ) from exc
        if not isinstance(body, dict):
            raise DependencyUnavailableError(
                "CallCraft returned an invalid response envelope.",
                code="CALLCRAFT_INVALID_RESPONSE",
            )
        return CallCraftOutcome(status_code=response.status_code, body=body)


def config_from_settings(settings: Any) -> CallCraftConfig:
    """Build the client only from trusted backend settings."""
    values = {
        "base_url": settings.callcraft_base_url,
        "user_id": settings.callcraft_user_id,
        "public_key": settings.callcraft_public_key,
        "auth_key": settings.callcraft_auth,
        "project_id": settings.callcraft_project_id,
        "timeout_seconds": settings.callcraft_timeout_seconds,
    }
    if any(
        not isinstance(value, str) or not value.strip()
        for key, value in values.items()
        if key != "timeout_seconds"
    ):
        raise ValueError("CallCraft trusted configuration is incomplete")
    return CallCraftConfig(**values)

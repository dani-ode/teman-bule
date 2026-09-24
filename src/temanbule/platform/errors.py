"""Typed application errors dengan HTTP mapping dan stable codes.

Sesuai api-events.md: error envelope {"error": {code, message, request_id, details}}.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base error aplikasi. message boleh diekspos; jangan lempar detail sensitif."""

    status_code: int = 500
    code: str = "INTERNAL"

    def __init__(
        self,
        message: str = "Terjadi kesalahan internal.",
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or []


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(AppError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "STATE_CONFLICT"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"


class DependencyUnavailableError(AppError):
    status_code = 503
    code = "DEPENDENCY_UNAVAILABLE"


class IdempotencyConflictError(ConflictError):
    code = "IDEMPOTENCY_CONFLICT"


class FeatureUnavailableError(AppError):
    status_code = 503
    code = "FEATURE_UNAVAILABLE"

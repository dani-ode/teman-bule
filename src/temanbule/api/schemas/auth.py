"""Auth request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class GenericAcceptedResponse(BaseModel):
    status: str = "accepted"


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ResendRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type literal
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class GoogleStartResponse(BaseModel):
    authorization_url: str


class GoogleCallbackQuery(BaseModel):
    code: str
    state: str

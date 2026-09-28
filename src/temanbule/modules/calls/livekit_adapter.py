"""Adapter konkret LiveKit token factory (DEC-14): join token JWT.

Menyediakan factory callable sesuai kontrak router calls
(``temanbule.api.routers.calls``):

    ``livekit_token_factory(session_id, user_id) -> str``

Callable sinkron (``Callable[[str, str], str]``) persis seperti dipakai
router; pembuatan token adalah operasi signing lokal (HS256) tanpa request
jaringan, sehingga aman dipanggil dari handler async.

Implementasi memakai LiveKit server SDK (``livekit-api``, ada di
requirements.txt) — ``AccessToken`` menghasilkan JWT HS256 dengan video grant
(``roomJoin``, ``room`` scoped per session), ``identity`` = user id, dan
``exp`` dari ``livekit_token_ttl_seconds``. API secret tidak pernah masuk log
maupun pesan error. Kegagalan signing → ``DependencyUnavailableError``.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable

from livekit import api

from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


class LiveKitTokenFactory:
    """Penghasil LiveKit access token scoped per room session dan user."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        token_ttl_seconds: int,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._token_ttl_seconds = int(token_ttl_seconds)

    def create_token(self, session_id: str, user_id: str) -> str:
        """Buat join token untuk room ``call-{session_id}`` milik satu user.

        Grant dibatasi: join room tunggal sesuai pola penamaan CallService
        (``f"call-{conversation.id}"`` dengan ``session_id == conversation.id``),
        tanpa hak admin/record. Kegagalan → DependencyUnavailableError.
        """
        if not session_id.strip() or not user_id.strip():
            raise DependencyUnavailableError(
                "Session atau user tidak valid untuk join token.",
                code="LIVEKIT_TOKEN_INPUT_INVALID",
            )
        try:
            token = (
                api.AccessToken(self._api_key, self._api_secret)
                .with_identity(user_id)
                .with_ttl(datetime.timedelta(seconds=self._token_ttl_seconds))
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=f"call-{session_id}",
                    )
                )
                .to_jwt()
            )
        except Exception as exc:  # SDK melempar tipe beragam; pesan dirahasiakan
            raise DependencyUnavailableError(
                "Gagal membuat LiveKit join token.",
                code="LIVEKIT_TOKEN_SIGN_FAILED",
            ) from exc
        return token


def build_livekit_token_factory(settings: Settings) -> Callable[[str, str], str]:
    """Callable ``(session_id, user_id) -> join token`` untuk app.state.

    Raise ValueError bila konfigurasi LiveKit kosong (nama variable saja,
    tanpa nilai/secret).
    """
    required = {
        "livekit_api_key": settings.livekit_api_key,
        "livekit_api_secret": settings.livekit_api_secret,
    }
    missing = [
        name for name, value in required.items() if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError("Konfigurasi LiveKit tidak lengkap: " + ", ".join(sorted(missing)))
    if settings.livekit_token_ttl_seconds <= 0:
        raise ValueError("LIVEKIT_TOKEN_TTL_SECONDS harus positif")
    factory = LiveKitTokenFactory(
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        token_ttl_seconds=settings.livekit_token_ttl_seconds,
    )
    return factory.create_token

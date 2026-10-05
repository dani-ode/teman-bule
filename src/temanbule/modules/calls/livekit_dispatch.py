"""LiveKit agent dispatch adapter (DEC-14): explicit dispatch per room.

Room LiveKit baru dibuat saat peserta pertama join; tanpa dispatch eksplisit,
LiveKit Cloud tidak mengirim agent ke room dan peserta menunggu tanpa jawaban
("waiting for agent"). Adapter ini mengirim ``CreateAgentDispatch`` untuk
room call tepat setelah admission berhasil, membawa metadata ULID session
agar job handler worker dapat menyelesaikan konfigurasi dari database
otoritatif — metadata bukan sumber kebenaran domain.

Kegagalan jaringan/SDK → ``DependencyUnavailableError`` tanpa membocorkan
API secret pada pesan error.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from livekit import api

from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


class LiveKitAgentDispatcher:
    """Mengirim explicit agent dispatch ke LiveKit server untuk satu room."""

    def __init__(self, *, url: str, api_key: str, api_secret: str, agent_name: str) -> None:
        if not agent_name.strip():
            raise ValueError("agent_name wajib untuk explicit dispatch")
        self._url = url
        self._api_key = api_key
        self._api_secret = api_secret
        self._agent_name = agent_name

    async def dispatch(self, *, room_name: str, session_id: str) -> None:
        """Dispatch agent bernama ke ``room_name`` dengan metadata session ULID."""
        if not room_name.strip() or not session_id.strip():
            raise DependencyUnavailableError(
                "Room atau session tidak valid untuk agent dispatch.",
                code="LIVEKIT_DISPATCH_INPUT_INVALID",
            )
        lkapi = api.LiveKitAPI(self._url, self._api_key, self._api_secret)
        try:
            await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=self._agent_name,
                    room=room_name,
                    metadata=session_id,
                )
            )
        except DependencyUnavailableError:
            raise
        except Exception as exc:  # SDK melempar tipe beragam; pesan dirahasiakan
            raise DependencyUnavailableError(
                "Gagal mengirim LiveKit agent dispatch.",
                code="LIVEKIT_DISPATCH_FAILED",
            ) from exc
        finally:
            await lkapi.aclose()


def build_livekit_agent_dispatcher(
    settings: Settings,
) -> Callable[..., Awaitable[None]]:
    """Callable ``(room_name, session_id) -> None`` untuk app.state.

    Raise ValueError bila konfigurasi LiveKit kosong (nama variable saja).
    """
    required = {
        "livekit_url": settings.livekit_url,
        "livekit_api_key": settings.livekit_api_key,
        "livekit_api_secret": settings.livekit_api_secret,
        "livekit_agent_name": settings.livekit_agent_name,
    }
    missing = [
        name for name, value in required.items() if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError(
            "Konfigurasi LiveKit dispatch tidak lengkap: " + ", ".join(sorted(missing))
        )
    dispatcher = LiveKitAgentDispatcher(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        agent_name=settings.livekit_agent_name,
    )
    return dispatcher.dispatch

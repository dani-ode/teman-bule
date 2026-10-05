"""Realtime worker entry point (Phase 6 / DEC-14).

Proses LiveKit Agents yang menjalankan voice call: VAD (silero lokal) →
STT (LiveKit Cloud Inference) → LLM streaming (OpenAI-compatible) →
ElevenLabs TTS. Worker mendaftar dengan ``agent_name`` sehingga room dibuat
via explicit dispatch (``POST /v1/calls`` → ``livekit_agent_dispatch``)
mendapat job.

Fail-fast: konfigurasi wajib divalidasi penuh sebelum worker berjalan
(``validate_for_realtime_worker``); tanpa fallback/mock tersembunyi.

Jalankan: ``python -m temanbule.realtime.main`` (sama dengan command service
``realtime-worker`` di compose.yaml). Worker CLI LiveKit membaca env
LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET yang sudah diset dari settings
sebelum ``run_app`` dipanggil.
"""

from __future__ import annotations

import os
import sys

from livekit.agents import cli
from livekit.agents.worker import WorkerOptions

from temanbule.platform.logging import configure_logging
from temanbule.platform.settings import load_settings
from temanbule.realtime.call_session import agent_entrypoint


def main() -> None:
    settings = load_settings(validate=False)
    settings.validate_for_realtime_worker()
    configure_logging(settings.app_log_level)

    # LiveKit worker CLI membaca kredensial dari environment; settings adalah
    # satu-satunya sumber kebenaran sehingga kita set eksplisit di sini.
    os.environ["LIVEKIT_URL"] = settings.livekit_url
    os.environ["LIVEKIT_API_KEY"] = settings.livekit_api_key
    os.environ["LIVEKIT_API_SECRET"] = settings.livekit_api_secret

    options = WorkerOptions(
        entrypoint_fnc=agent_entrypoint,
        agent_name=settings.livekit_agent_name,
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        drain_timeout=max(settings.realtime_shutdown_grace_seconds, 1),
        shutdown_process_timeout=max(settings.realtime_shutdown_grace_seconds, 1),
        # Satu job per proses menjaga isolasi credential BYOK antar session.
        num_idle_processes=1,
    )
    # Default subcommand "start" saat dipanggil tanpa argumen (compose).
    if len(sys.argv) == 1:
        sys.argv.append("start")
    cli.run_app(options)


if __name__ == "__main__":
    main()

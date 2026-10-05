"""Unit tests untuk realtime call plumbing (Phase 6 / DEC-14).

Test doubles di sini HANYA memverifikasi kontrak internal (bukan kompatibilitas
vendor): dispatch adapter, provider config loader, LLM adapter, settings
worker validation, dan wiring router.
"""

from __future__ import annotations

import pytest

from temanbule.modules.calls.livekit_dispatch import (
    LiveKitAgentDispatcher,
    build_livekit_agent_dispatcher,
)
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import ConfigurationError, Settings
from temanbule.realtime.llm_adapter import OpenAICompatibleLLM, chat_completions_url


def _settings(**overrides: object) -> Settings:
    base = {
        "livekit_url": "wss://example.test",
        "livekit_api_key": "key",
        "livekit_api_secret": "secret",
        "livekit_agent_name": "temanbule-call-agent",
    }
    base.update(overrides)
    return Settings.model_construct(**base)


class TestBuildLivekitAgentDispatcher:
    def test_missing_config_raises_with_variable_names(self) -> None:
        settings = _settings(livekit_api_key="", livekit_agent_name="")
        with pytest.raises(ValueError) as excinfo:
            build_livekit_agent_dispatcher(settings)
        message = str(excinfo.value)
        assert "livekit_api_key" in message
        assert "livekit_agent_name" in message
        # Tidak membocorkan nilai yang terisi.
        assert "wss://example.test" not in message

    def test_builds_callable(self) -> None:
        dispatch = build_livekit_agent_dispatcher(_settings())
        assert callable(dispatch)


class TestLiveKitAgentDispatcher:
    @pytest.mark.asyncio
    async def test_blank_input_rejected(self) -> None:
        dispatcher = LiveKitAgentDispatcher(
            url="wss://example.test", api_key="k", api_secret="s", agent_name="a"  # noqa: S106 - synthetic fixture
        )
        with pytest.raises(DependencyUnavailableError) as excinfo:
            await dispatcher.dispatch(room_name=" ", session_id="x")
        assert excinfo.value.code == "LIVEKIT_DISPATCH_INPUT_INVALID"

    def test_blank_agent_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            LiveKitAgentDispatcher(
                url="wss://example.test", api_key="k", api_secret="s", agent_name=" "  # noqa: S106 - synthetic fixture
            )


class TestChatCompletionsUrl:
    def test_gemini_mapping(self) -> None:
        assert chat_completions_url("gemini", "https://generativelanguage.googleapis.com") == (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        )

    def test_openai_mapping(self) -> None:
        assert chat_completions_url("openai", "https://api.openai.com/v1/") == (
            "https://api.openai.com/v1/chat/completions"
        )

    def test_unknown_provider_rejected(self) -> None:
        with pytest.raises(ValueError):
            chat_completions_url("anthropic", "https://api.test")


class TestOpenAICompatibleLLM:
    def test_blank_config_rejected(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleLLM(
                provider="gemini", model=" ", base_url="https://x", api_key="k", timeout_seconds=5
            )
        with pytest.raises(ValueError):
            OpenAICompatibleLLM(
                provider="gemini", model="m", base_url="https://x", api_key="", timeout_seconds=5
            )

    def test_tool_calling_rejected_explicitly(self) -> None:
        from livekit.agents.llm import ChatContext
        from livekit.agents.llm.tool_context import function_tool

        @function_tool
        def _dummy() -> str:
            return "ok"

        llm_instance = OpenAICompatibleLLM(
            provider="gemini",
            model="gemini-3.8-flash",
            base_url="https://generativelanguage.googleapis.com",
            api_key="k",
            timeout_seconds=5,
        )
        with pytest.raises(NotImplementedError):
            llm_instance.chat(chat_ctx=ChatContext(), tools=[_dummy])  # type: ignore[list-item]


class TestRealtimeWorkerSettings:
    def test_worker_requires_feature_flag(self) -> None:
        settings = Settings(feature_realtime_call_enabled=False)
        with pytest.raises(ConfigurationError) as excinfo:
            settings.validate_for_realtime_worker()
        assert any("FEATURE_REALTIME_CALL_ENABLED" in p for p in excinfo.value.problems)

    def test_provider_base_url_mapping(self) -> None:
        settings = Settings()
        assert settings.provider_base_url("gemini") == settings.gemini_base_url
        assert settings.provider_base_url("openai") == settings.openai_base_url
        with pytest.raises(ConfigurationError):
            settings.provider_base_url("unknown")

    def test_agent_name_required_when_realtime_enabled(self) -> None:
        settings = Settings(
            feature_realtime_call_enabled=True,
            feature_media_enabled=True,
            livekit_url="wss://x",
            livekit_api_key="k",
            livekit_api_secret="s",  # noqa: S106 - synthetic fixture
            livekit_agent_name="",
            tts_api_key="t",
            tts_model="m",
            tts_voice_id_elean="e",
            tts_voice_id_willy="w",
            call_vad_silence_ms=1000,
            call_max_duration_seconds=100,
            call_reconnect_grace_seconds=10,
            realtime_max_concurrent_sessions=1,
            realtime_lease_ttl_seconds=10,
        )
        with pytest.raises(ConfigurationError) as excinfo:
            settings.validate_for_api()
        assert "LIVEKIT_AGENT_NAME" in excinfo.value.problems

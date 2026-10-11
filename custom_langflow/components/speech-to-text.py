import base64
import io
from typing import Any

import httpx

from lfx.custom import Component
from lfx.io import (
    DropdownInput,
    MessageTextInput,
    MultilineInput,
    Output,
    SecretStrInput,
    StrInput,
)
from lfx.schema import Data
from lfx.schema.message import Message


class SpeechToTextComponent(Component):
    display_name = "Speech to Text"
    description = (
        "Transcribes Base64 encoded audio into text and returns "
        "the transcription as a Langflow Message."
    )
    documentation = ""
    icon = "mic"
    name = "SpeechToText"

    inputs = [
        DropdownInput(
            name="provider",
            display_name="Provider",
            info="Select the speech-to-text provider.",
            options=[
                "OpenAI",
                "OpenAI Compatible",
            ],
            value="OpenAI",
            real_time_refresh=True,
            required=True,
        ),

        DropdownInput(
            name="model",
            display_name="Model",
            info="Select the speech-to-text model.",
            options=[
                "whisper-1",
                "gpt-4o-mini-transcribe",
                "gpt-4o-transcribe",
            ],
            value="whisper-1",
            required=True,
        ),

        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="API key for the selected speech-to-text provider.",
            required=True,
            show=True,
        ),

        StrInput(
            name="base_url",
            display_name="Base URL",
            info=(
                "API base URL. For OpenAI use "
                "https://api.openai.com/v1"
            ),
            value="https://api.openai.com/v1",
            required=True,
        ),

        MessageTextInput(
            name="audio_base64",
            display_name="Audio Base64",
            info=(
                "Base64 encoded audio. Supports plain Base64 "
                "or a data URL such as data:audio/wav;base64,..."
            ),
            required=True,
        ),

        StrInput(
            name="file_name",
            display_name="File Name",
            info="Audio filename including extension.",
            value="audio.wav",
            required=True,
        ),

        StrInput(
            name="content_type",
            display_name="Content Type",
            info=(
                "MIME type of the audio. Examples: "
                "audio/wav, audio/mpeg, audio/mp4, audio/webm."
            ),
            value="audio/wav",
            required=True,
        ),

        StrInput(
            name="language",
            display_name="Language",
            info=(
                "Optional ISO-639-1 language code, for example "
                "'en' or 'id'. Leave empty for automatic detection."
            ),
            required=False,
        ),

        MultilineInput(
            name="prompt",
            display_name="Prompt",
            info=(
                "Optional context or vocabulary hint for transcription."
            ),
            required=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="message",
            display_name="Message",
            method="transcribe",
        ),
        Output(
            name="result",
            display_name="Result",
            method="transcribe_result",
        ),
    ]

    def _decode_audio(self) -> bytes:
        """Decode Base64 audio into raw bytes."""

        value = self.audio_base64

        if not value:
            raise ValueError("Audio Base64 is empty.")

        # Support:
        #
        # data:audio/wav;base64,UklGR...
        #
        # and:
        #
        # UklGR...

        if value.startswith("data:"):
            if "," not in value:
                raise ValueError("Invalid Base64 data URL.")

            value = value.split(",", 1)[1]

        # Remove whitespace and newlines
        value = "".join(value.split())

        try:
            return base64.b64decode(value, validate=True)
        except Exception as exc:
            raise ValueError(
                "Invalid Base64 audio data."
            ) from exc

    def _get_endpoint(self) -> str:
        """Build the transcription endpoint."""

        base_url = self.base_url.rstrip("/")

        return f"{base_url}/audio/transcriptions"

    def _transcribe(self) -> dict[str, Any]:
        """Send audio to the STT provider."""

        audio_bytes = self._decode_audio()

        endpoint = self._get_endpoint()

        files = {
            "file": (
                self.file_name,
                io.BytesIO(audio_bytes),
                self.content_type,
            )
        }

        data = {
            "model": self.model,
        }

        language = (self.language or "").strip()

        if language:
            data["language"] = language

        prompt = (self.prompt or "").strip()

        if prompt:
            data["prompt"] = prompt

        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            response = httpx.post(
                endpoint,
                headers=headers,
                data=data,
                files=files,
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Failed to connect to STT provider: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise RuntimeError(
                f"STT API returned HTTP {response.status_code}: "
                f"{response.text}"
            )

        try:
            result = response.json()
        except Exception as exc:
            raise RuntimeError(
                f"STT API returned invalid JSON: {response.text}"
            ) from exc

        return result

    def transcribe(self) -> Message:
        """Return transcription as a Langflow Message."""

        result = self._transcribe()

        text = result.get("text")

        if text is None:
            raise RuntimeError(
                f"STT response does not contain 'text': {result}"
            )

        return Message(
            text=text,
        )

    def transcribe_result(self) -> Data:
        """Return the complete provider response."""

        result = self._transcribe()

        return Data(data=result)
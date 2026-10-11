from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from lfx.custom import Component
from lfx.io import (
    BoolInput,
    DropdownInput,
    FloatInput,
    MultilineInput,
    Output,
    SecretStrInput,
    StrInput,
)
from lfx.schema import Data


class ElevenLabsTTS(Component):
    display_name = "ElevenLabs TTS"
    description = "Convert text to speech using the ElevenLabs Text-to-Speech API."
    documentation = (
        "https://elevenlabs.io/docs/api-reference/text-to-speech/convert"
    )
    icon = "ElevenLabs"
    name = "elevenlabs_tts"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="Your ElevenLabs API key.",
            required=True,
        ),

        StrInput(
            name="voice_id",
            display_name="Voice ID",
            info="ElevenLabs Voice ID.",
            required=True,
            input_types=["Text", "Message"], # Bisa menerima Text atau Message
        ),

        StrInput(
            name="model_id",
            display_name="Model ID",
            info="ElevenLabs TTS Model ID.",
            value="eleven_v3",
            required=True,
            input_types=["Text", "Message"], # Bisa menerima Text atau Message
        ),

        MultilineInput(
            name="text_input",
            display_name="Text",
            info="Text to convert to speech.",
            required=True,
            input_types=["Text", "Message"], # Sangat berguna untuk menerima output dari node LLM
        ),

        DropdownInput(
            name="output_format",
            display_name="Output Format",
            info="Audio format returned by ElevenLabs.",
            options=[
                "mp3_44100_128",
                "mp3_44100_192",
                "mp3_22050_32",
                "mp3_22050_64",
                "mp3_44100_64",
                "pcm_16000",
                "pcm_22050",
                "pcm_24000",
                "pcm_44100",
            ],
            value="mp3_44100_128",
        ),

        StrInput(
            name="language_code",
            display_name="Language Code",
            info="Optional language code such as en or id.",
            value="",
            advanced=True,
        ),

        FloatInput(
            name="stability",
            display_name="Stability",
            info="Voice stability from 0 to 1.",
            value=0.5,
            advanced=True,
        ),

        FloatInput(
            name="similarity_boost",
            display_name="Similarity Boost",
            info="Voice similarity from 0 to 1.",
            value=0.75,
            advanced=True,
        ),

        FloatInput(
            name="style",
            display_name="Style",
            info="Style exaggeration from 0 to 1.",
            value=0.0,
            advanced=True,
        ),

        BoolInput(
            name="use_speaker_boost",
            display_name="Speaker Boost",
            info="Enable speaker boost when supported.",
            value=True,
            advanced=True,
        ),

        FloatInput(
            name="speed",
            display_name="Speed",
            info="Speech speed multiplier.",
            value=1.0,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="audio_data",
            display_name="Audio",
            method="build_audio",
            group_outputs=True,
        ),
        Output(
            name="metadata",
            display_name="Metadata",
            method="build_metadata",
            group_outputs=True,
        ),
    ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_elevenlabs_api_key(self) -> str:
        """Get the API key from SecretStrInput safely."""
        value = self.api_key

        if value is None:
            return ""

        if hasattr(value, "get_secret_value"):
            return value.get_secret_value()

        return str(value)

    def _get_string_value(self, field_value: Any) -> str:
        """Helper to ensure we extract a string even if a Message object is passed."""
        if field_value is None:
            return ""
        
        # Jika framework lfx menggunakan format Message (seperti Langflow)
        if hasattr(field_value, "text"):
            return str(field_value.text)
            
        return str(field_value)

    def _validate_tts_inputs(self) -> None:
        """Validate ElevenLabs-specific inputs."""
        api_key = self._get_elevenlabs_api_key()
        voice_id = self._get_string_value(self.voice_id).strip()
        model_id = self._get_string_value(self.model_id).strip()
        text = self._get_string_value(self.text_input).strip()

        if not api_key:
            raise ValueError("ElevenLabs API Key is required.")
        if not voice_id:
            raise ValueError("ElevenLabs Voice ID is required.")
        if not model_id:
            raise ValueError("ElevenLabs Model ID is required.")
        if not text:
            raise ValueError("Text cannot be empty.")

        stability = float(self.stability)
        if not 0.0 <= stability <= 1.0:
            raise ValueError("Stability must be between 0 and 1.")

        similarity_boost = float(self.similarity_boost)
        if not 0.0 <= similarity_boost <= 1.0:
            raise ValueError("Similarity Boost must be between 0 and 1.")

        style = float(self.style)
        if not 0.0 <= style <= 1.0:
            raise ValueError("Style must be between 0 and 1.")

        speed = float(self.speed)
        if speed <= 0:
            raise ValueError("Speed must be greater than 0.")

    def _get_mime_type(self) -> str:
        """Return MIME type based on selected output format."""
        output_format = str(self.output_format or "").lower()

        if output_format.startswith("mp3"):
            return "audio/mpeg"
        if output_format.startswith("pcm"):
            return "audio/pcm"
        if output_format.startswith("ulaw"):
            return "audio/basic"

        return "application/octet-stream"

    def _calculate_audio_duration(self, audio_bytes: bytes, output_format: str) -> float | None:
        """
        Estimate audio duration in milliseconds if possible.
        Only calculates exactly for PCM formats. For MP3 it returns None
        because computing MP3 duration requires parsing audio frames via external library.
        """
        if output_format.startswith("pcm_"):
            try:
                # Format is usually 'pcm_16000', 'pcm_24000', dll.
                sample_rate = int(output_format.split("_")[1])
                # ElevenLabs PCM is 16-bit mono = 2 bytes per sample
                bytes_per_sample = 2
                duration_seconds = len(audio_bytes) / (sample_rate * bytes_per_sample)
                return round(duration_seconds * 1000, 2)
            except (IndexError, ValueError):
                return None
        return None

    # ------------------------------------------------------------------
    # ElevenLabs API
    # ------------------------------------------------------------------

    def _request_elevenlabs_audio(self) -> bytes:
        """Send TTS request to ElevenLabs."""
        self._validate_tts_inputs()

        api_key = self._get_elevenlabs_api_key()
        voice_id = self._get_string_value(self.voice_id).strip()
        model_id = self._get_string_value(self.model_id).strip()
        text = self._get_string_value(self.text_input)
        output_format = str(self.output_format).strip()

        encoded_voice_id = urllib.parse.quote(voice_id, safe="")
        encoded_output_format = urllib.parse.quote(output_format, safe="")

        url = (
            "https://api.elevenlabs.io/v1/text-to-speech/"
            f"{encoded_voice_id}"
            f"?output_format={encoded_output_format}"
        )

        voice_settings: dict[str, Any] = {
            "stability": float(self.stability),
            "similarity_boost": float(self.similarity_boost),
            "style": float(self.style),
            "use_speaker_boost": bool(self.use_speaker_boost),
        }

        # Only add speed when it differs from default.
        if float(self.speed) != 1.0:
            voice_settings["speed"] = float(self.speed)

        payload: dict[str, Any] = {
            "text": text,
            "model_id": model_id,
            "voice_settings": voice_settings,
        }

        language_code = str(self.language_code or "").strip()
        if language_code:
            payload["language_code"] = language_code

        request_body = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=request_body,
            method="POST",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": self._get_mime_type(),
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                audio = response.read()
                if not audio:
                    raise RuntimeError("ElevenLabs returned an empty audio response.")
                return audio

        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed_error = json.loads(error_body)
                detail = parsed_error.get("detail", parsed_error)
                if isinstance(detail, dict):
                    error_message = detail.get(
                        "message",
                        detail.get("status", str(detail)),
                    )
                else:
                    error_message = str(detail)
            except json.JSONDecodeError:
                error_message = error_body
            raise RuntimeError(f"ElevenLabs API error ({exc.code}): {error_message}") from exc

        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not connect to ElevenLabs: {exc.reason}") from exc

    # ------------------------------------------------------------------
    # Audio cache
    # ------------------------------------------------------------------

    def _get_generated_audio(self) -> bytes:
        """
        Generate audio once and reuse it for all outputs.
        """
        if "elevenlabs_audio" not in self.ctx:
            self.ctx["elevenlabs_audio"] = self._request_elevenlabs_audio()
        return self.ctx["elevenlabs_audio"]

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------

    def build_audio(self) -> Data:
        """Return generated audio."""
        audio = self._get_generated_audio()
        audio_base64 = base64.b64encode(audio).decode("ascii")
        output_format_str = str(self.output_format)
        
        audio_duration = self._calculate_audio_duration(audio, output_format_str)

        result = {
            "audio_base64": audio_base64,
            "mime_type": self._get_mime_type(),
            "output_format": output_format_str,
            "voice_id": self._get_string_value(self.voice_id),
            "model_id": self._get_string_value(self.model_id),
            "response_text": self._get_string_value(self.text_input),  # DIUBAH DARI "text"
            "size_bytes": len(audio),
            "audio_duration_ms": audio_duration,                       # DITAMBAHKAN
        }

        self.status = f"ElevenLabs TTS generated {len(audio):,} bytes."

        return Data(data=result)

    def build_metadata(self) -> Data:
        """Return metadata together with audio information."""
        audio = self._get_generated_audio()
        text_str = self._get_string_value(self.text_input)
        output_format_str = str(self.output_format)
        
        audio_duration = self._calculate_audio_duration(audio, output_format_str)

        result = {
            "provider": "elevenlabs",
            "type": "text_to_speech",
            "voice_id": self._get_string_value(self.voice_id),
            "model_id": self._get_string_value(self.model_id),
            "output_format": output_format_str,
            "language_code": (
                str(self.language_code).strip() if self.language_code else None
            ),
            "response_text": text_str,             # DIUBAH DARI "text"
            "text_length": len(text_str),
            "audio_size_bytes": len(audio),
            "mime_type": self._get_mime_type(),
            "audio_duration_ms": audio_duration,   # DITAMBAHKAN
        }

        return Data(data=result)
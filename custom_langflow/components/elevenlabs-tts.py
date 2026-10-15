from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
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
            input_types=["Text", "Message"],
        ),

        StrInput(
            name="model_id",
            display_name="Model ID",
            info="ElevenLabs TTS Model ID.",
            value="eleven_v3",
            required=True,
            input_types=["Text", "Message"],
        ),

        MultilineInput(
            name="text_input",
            display_name="Text",
            info="Text to convert to speech.",
            required=True,
            input_types=["Text", "Message"],
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

    @staticmethod
    def _parse_mp3_duration(file_bytes: bytes) -> float | None:
        """
        Mengekstrak durasi MP3 (dalam detik) murni dalam Python tanpa dependensi eksternal.
        """
        try:
            length = len(file_bytes)
            if length < 10:
                return None

            offset = 0
            if file_bytes.startswith(b"ID3"):
                id3_size = (
                    ((file_bytes[6] & 0x7F) << 21)
                    | ((file_bytes[7] & 0x7F) << 14)
                    | ((file_bytes[8] & 0x7F) << 7)
                    | (file_bytes[9] & 0x7F)
                )
                offset = 10 + id3_size

            bitrates_v1_l3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
            bitrates_v2_l3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
            sample_rates_v1 = [44100, 48000, 32000]
            sample_rates_v2 = [22050, 24000, 16000]
            sample_rates_v25 = [11025, 12000, 8000]

            while offset < length - 4:
                b0 = file_bytes[offset]
                b1 = file_bytes[offset + 1]

                if b0 == 0xFF and (b1 & 0xE0) == 0xE0:
                    version_bits = (b1 >> 3) & 0x03
                    b2 = file_bytes[offset + 2]
                    bitrate_index = (b2 >> 4) & 0x0F
                    sr_index = (b2 >> 2) & 0x03

                    if bitrate_index not in (0, 15) and sr_index != 3:
                        if version_bits == 3:
                            bitrate = bitrates_v1_l3[bitrate_index] * 1000
                            sr = sample_rates_v1[sr_index]
                        elif version_bits == 2:
                            bitrate = bitrates_v2_l3[bitrate_index] * 1000
                            sr = sample_rates_v2[sr_index]
                        else:
                            bitrate = bitrates_v2_l3[bitrate_index] * 1000
                            sr = sample_rates_v25[sr_index]

                        frame_data = file_bytes[offset: offset + 200]
                        xing_pos = frame_data.find(b"Xing")
                        if xing_pos == -1:
                            xing_pos = frame_data.find(b"Info")

                        if xing_pos != -1:
                            flags = int.from_bytes(frame_data[xing_pos + 4: xing_pos + 8], "big")
                            if flags & 1:
                                num_frames = int.from_bytes(frame_data[xing_pos + 8: xing_pos + 12], "big")
                                samples_per_frame = 1152 if version_bits == 3 else 576
                                return (num_frames * samples_per_frame) / float(sr)

                        audio_size = length - offset
                        if bitrate > 0:
                            return (audio_size * 8) / float(bitrate)
                offset += 1
        except Exception:
            pass
        return None

    @staticmethod
    def _calculate_audio_duration_ms(audio_bytes: bytes, output_format: str) -> int | None:
        """
        Menghitung durasi audio dalam MILIDETIK (milliseconds).
        Mendukung PCM, MP3 (Native Frame Parser & Fallback Bitrate Calculation).
        """
        if not audio_bytes:
            return None

        duration_sec: float | None = None
        fmt = str(output_format or "").lower().strip()

        # 1. Format PCM (ElevenLabs PCM is 16-bit mono = 2 bytes per sample)
        if fmt.startswith("pcm_"):
            try:
                parts = fmt.split("_")
                sample_rate = int(parts[1])
                bytes_per_sample = 2
                duration_sec = len(audio_bytes) / (sample_rate * bytes_per_sample)
            except (IndexError, ValueError):
                pass

        # 2. Format MP3 - Native Frame Parser
        if duration_sec is None and (fmt.startswith("mp3") or "mpeg" in fmt):
            duration_sec = ElevenLabsTTS._parse_mp3_duration(audio_bytes)

            # Fallback jika frame MP3 tidak terdeteksi: hitung via bitrate output_format (e.g. mp3_44100_128 -> 128 kbps)
            if duration_sec is None and fmt.startswith("mp3_"):
                try:
                    parts = fmt.split("_")
                    if len(parts) >= 3 and parts[2].isdigit():
                        bitrate_kbps = int(parts[2])
                        if bitrate_kbps > 0:
                            bytes_per_sec = (bitrate_kbps * 1000) / 8.0
                            duration_sec = len(audio_bytes) / bytes_per_sec
                except Exception:
                    pass

        # 3. Fallback: Mutagen (jika terinstal)
        if duration_sec is None:
            try:
                from mutagen import File as MutagenFile
                media = MutagenFile(BytesIO(audio_bytes))
                if media is not None and getattr(media, "info", None) is not None:
                    length = getattr(media.info, "length", None)
                    if length is not None:
                        duration_sec = float(length)
            except Exception:
                pass

        if duration_sec is not None and duration_sec >= 0:
            return int(round(duration_sec * 1000.0))

        return None

    @staticmethod
    def _format_duration(ms: int | None) -> str | None:
        """Format milidetik menjadi HH:MM:SS atau MM:SS."""
        if ms is None:
            return None
        total_seconds = int(round(ms / 1000.0))
        hrs = total_seconds // 3600
        mins = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        if hrs > 0:
            return f"{hrs:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

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
        
        audio_duration_ms = self._calculate_audio_duration_ms(audio, output_format_str)
        duration_formatted = self._format_duration(audio_duration_ms)

        result = {
            "audio_base64": audio_base64,
            "mime_type": self._get_mime_type(),
            "output_format": output_format_str,
            "voice_id": self._get_string_value(self.voice_id),
            "model_id": self._get_string_value(self.model_id),
            "response_text": self._get_string_value(self.text_input),
            "size_bytes": len(audio),
            "audio_duration_ms": audio_duration_ms,        # Angka milidetik (contoh: 5350)
            "duration_ms": audio_duration_ms,              # Angka milidetik (contoh: 5350)
            "duration_formatted": duration_formatted,     # Format menit:detik (contoh: "00:05")
        }

        self.status = f"ElevenLabs TTS generated {len(audio):,} bytes."

        return Data(data=result)

    def build_metadata(self) -> Data:
        """Return metadata together with audio information."""
        audio = self._get_generated_audio()
        text_str = self._get_string_value(self.text_input)
        output_format_str = str(self.output_format)
        
        audio_duration_ms = self._calculate_audio_duration_ms(audio, output_format_str)
        duration_formatted = self._format_duration(audio_duration_ms)

        result = {
            "provider": "elevenlabs",
            "type": "text_to_speech",
            "voice_id": self._get_string_value(self.voice_id),
            "model_id": self._get_string_value(self.model_id),
            "output_format": output_format_str,
            "language_code": (
                str(self.language_code).strip() if self.language_code else None
            ),
            "response_text": text_str,
            "text_length": len(text_str),
            "audio_size_bytes": len(audio),
            "mime_type": self._get_mime_type(),
            "audio_duration_ms": audio_duration_ms,        # Angka milidetik (contoh: 5350)
            "duration_ms": audio_duration_ms,              # Angka milidetik (contoh: 5350)
            "duration_formatted": duration_formatted,     # Format menit:detik (contoh: "00:05")
        }

        return Data(data=result)
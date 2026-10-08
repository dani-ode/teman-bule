"""Canvas boundary for structured application chat.

Unimplemented operations deliberately fail instead of returning invented data.
The source is embedded in exports so imports do not require repository mounts.
"""
import json

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MultilineInput, Output, StrInput
from lfx.schema.data import Data


class ChatCanvasStage(Component):
    display_name = "Teman Bule Chat Stage"
    name = "ChatCanvasStage"
    description = "Structured chat stage; unfinished integrations fail explicitly."
    inputs = [
        StrInput(name="operation", display_name="Operation", required=True),
        MultilineInput(name="payload", display_name="Application JSON", value=""),
        DataInput(name="upstream", display_name="Dependencies", is_list=True),
    ]
    outputs = [Output(name="result", display_name="Result", method="execute")]

    def execute(self) -> Data:
        if self.operation in {"json_input", "normalize_input"}:
            value = json.loads(self.payload)
            if not isinstance(value, dict):
                raise ValueError("Application input must be a JSON object")
            for field in ("request_id", "session", "user", "ai_configuration", "input"):
                if field not in value:
                    raise ValueError(f"Missing application input: {field}")
            if self.operation == "normalize_input":
                source = value["input"]
                if not isinstance(source, dict):
                    raise ValueError("input must be an object")
                modality = source.get("modality")
                if modality == "text" and not str(source.get("text") or "").strip():
                    raise ValueError("text modality requires non-empty text")
                if modality == "audio" and not any(source.get(key) for key in ("audio_media_id", "audio_url", "audio_base64")):
                    raise ValueError("audio modality requires a media reference")
                if modality not in {"text", "audio"}:
                    raise ValueError("input.modality must be text or audio")
            return Data(data=value)
        if self.operation == "response":
            value = json.loads(self.payload)
            if not isinstance(value, dict) or not isinstance(value.get("response_text"), str):
                raise ValueError("response requires response_text")
            value.setdefault("citations", [])
            value.setdefault("tool_outcomes", [])
            value["status"] = "completed"
            return Data(data=value)
        raise NotImplementedError(
            f"Chat stage '{self.operation}' has no runtime implementation. "
            "This draft canvas must not be activated."
        )

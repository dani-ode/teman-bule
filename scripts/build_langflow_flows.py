#!/usr/bin/env python3
"""Build all Teman Bule Langflow flows via the Langflow REST API.

Creates or updates all 16 workflow flows defined in the blueprint
(.blueprint/langflow-flows.md) on the local Langflow instance.

Usage:
    python scripts/build_langflow_flows.py
"""

import json
import sys
import httpx

BASE_URL = "http://localhost:7860"
API_KEY = "sk-oFBsG0WtoviiYH9rOZs2S93Djhi9egXA6LSSJrIYISM"
PROJECT_ID = "298095b5-c03d-4229-b96b-e4ad9389d394"
HEADERS = {"x-api-key": API_KEY, "Content-Type": "application/json"}

# ── helpers ────────────────────────────────────────────────────────────────

def _uid(prefix: str, name: str) -> str:
    return f"{prefix}-{name}"

def chat_input_node(node_id: str, x: int, y: int) -> dict:
    return {
        "id": node_id,
        "type": "genericNode",
        "position": {"x": x, "y": y},
        "data": {
            "id": node_id,
            "type": "ChatInput",
            "node": {
                "base_classes": ["Message"],
                "beta": False,
                "conditional_paths": [],
                "custom_fields": {},
                "description": "Get chat inputs from the Playground.",
                "display_name": "Chat Input",
                "documentation": "https://docs.langflow.org/chat-input-and-output",
                "edited": False,
                "field_order": ["input_value", "should_store_message", "sender", "sender_name", "session_id", "context_id", "files"],
                "frozen": False,
                "icon": "MessagesSquare",
                "legacy": False,
                "metadata": {},
                "minimized": True,
                "output_types": [],
                "outputs": [{
                    "allows_loop": False,
                    "cache": True,
                    "display_name": "Chat Message",
                    "group_outputs": False,
                    "method": "message_response",
                    "name": "message",
                    "selected": "Message",
                    "tool_mode": True,
                    "types": ["Message"],
                    "value": "__UNDEFINED__",
                }],
                "pinned": False,
                "template": {
                    "_type": "Component",
                    "code": {"type": "code", "value": ""},
                    "input_value": {"type": "str", "value": "", "_input_type": "MultilineInput", "input_types": [], "display_name": "Input Text", "name": "input_value", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "Message to be passed as input.", "title_case": False, "password": False, "multiline": True, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "should_store_message": {"type": "bool", "value": True, "_input_type": "BoolInput", "display_name": "Store Messages", "name": "should_store_message", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Store the message in the history.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "sender": {"type": "str", "value": "User", "_input_type": "DropdownInput", "options": ["Machine", "User"], "display_name": "Sender Type", "name": "sender", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Type of sender.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "sender_name": {"type": "str", "value": "User", "_input_type": "MessageTextInput", "display_name": "Sender Name", "name": "sender_name", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Name of the sender.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False, "input_types": ["Message"]},
                    "session_id": {"type": "str", "value": "", "_input_type": "MessageTextInput", "display_name": "Session ID", "name": "session_id", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The session ID of the chat.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False, "input_types": ["Message"]},
                    "context_id": {"type": "str", "value": "", "_input_type": "MessageTextInput", "display_name": "Context ID", "name": "context_id", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The context ID of the chat.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False, "input_types": ["Message"]},
                    "files": {"type": "file", "value": "", "_input_type": "FileInput", "display_name": "Files", "name": "files", "show": True, "advanced": True, "required": False, "list": True, "dynamic": False, "info": "Files to be sent with the message.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False, "file_types": []},
                },
            },
        },
    }

def chat_output_node(node_id: str, x: int, y: int) -> dict:
    return {
        "id": node_id,
        "type": "genericNode",
        "position": {"x": x, "y": y},
        "data": {
            "id": node_id,
            "type": "ChatOutput",
            "node": {
                "base_classes": ["Message"],
                "beta": False,
                "conditional_paths": [],
                "custom_fields": {},
                "description": "Display a chat message in the Playground.",
                "display_name": "Chat Output",
                "documentation": "https://docs.langflow.org/chat-input-and-output",
                "edited": False,
                "field_order": ["input_value", "should_store_message", "sender", "sender_name", "session_id", "context_id", "data_template", "background_color", "chat_icon", "text_color", "clean_data"],
                "frozen": False,
                "icon": "MessagesSquare",
                "legacy": False,
                "metadata": {},
                "minimized": True,
                "output_types": [],
                "outputs": [{
                    "allows_loop": False,
                    "cache": True,
                    "display_name": "Chat Message",
                    "group_outputs": False,
                    "method": "message_response",
                    "name": "message",
                    "selected": "Message",
                    "tool_mode": True,
                    "types": ["Message"],
                    "value": "__UNDEFINED__",
                }],
                "pinned": False,
                "template": {
                    "_type": "Component",
                    "code": {"type": "code", "value": ""},
                    "input_value": {"type": "other", "value": "", "_input_type": "MessageTextInput", "input_types": ["Data", "JSON", "DataFrame", "Table", "Message"], "display_name": "Text", "name": "input_value", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "Message to be passed as output.", "title_case": False, "password": False, "multiline": True, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "should_store_message": {"type": "bool", "value": True, "_input_type": "BoolInput", "display_name": "Store Messages", "name": "should_store_message", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Store the message in the history.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "sender": {"type": "str", "value": "Machine", "_input_type": "DropdownInput", "options": ["Machine", "User"], "display_name": "Sender Type", "name": "sender", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Type of sender.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "sender_name": {"type": "str", "value": "AI", "_input_type": "MessageTextInput", "display_name": "Sender Name", "name": "sender_name", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Name of the sender.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False, "input_types": ["Message"]},
                    "session_id": {"type": "str", "value": "", "_input_type": "MessageTextInput", "display_name": "Session ID", "name": "session_id", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The session ID of the chat.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False, "input_types": ["Message"]},
                    "data_template": {"type": "str", "value": "{text}", "_input_type": "MessageTextInput", "display_name": "Data Template", "name": "data_template", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Template to convert Data to Text.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "background_color": {"type": "str", "value": "", "_input_type": "MessageTextInput", "display_name": "Background Color", "name": "background_color", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The background color of the icon.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "chat_icon": {"type": "str", "value": "", "_input_type": "MessageTextInput", "display_name": "Icon", "name": "chat_icon", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The icon of the message.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "text_color": {"type": "str", "value": "", "_input_type": "MessageTextInput", "display_name": "Text Color", "name": "text_color", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The text color of the name", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "clean_data": {"type": "bool", "value": True, "_input_type": "BoolInput", "display_name": "Basic Clean Data", "name": "clean_data", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Cleans the data.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                },
            },
        },
    }

def gemini_node(node_id: str, x: int, y: int, system_message: str, temperature: float = 0.1, model: str = "gemini-2.0-flash") -> dict:
    return {
        "id": node_id,
        "type": "genericNode",
        "position": {"x": x, "y": y},
        "data": {
            "id": node_id,
            "type": "ext:google:GoogleGenerativeAIComponent@official",
            "node": {
                "base_classes": ["Message", "LanguageModel"],
                "beta": False,
                "conditional_paths": [],
                "custom_fields": {},
                "description": "Google Generative AI model.",
                "display_name": "Google Generative AI",
                "documentation": "",
                "edited": False,
                "field_order": ["api_key", "model_name", "max_output_tokens", "temperature", "top_p", "top_k", "n", "stream", "system_message", "tool_model_enabled", "input_value"],
                "frozen": False,
                "icon": "GoogleGenerativeAI",
                "legacy": False,
                "metadata": {},
                "minimized": True,
                "output_types": [],
                "outputs": [
                    {"allows_loop": False, "cache": True, "display_name": "Text", "group_outputs": False, "method": "text_response", "name": "text_output", "selected": "Message", "tool_mode": True, "types": ["Message"], "value": "__UNDEFINED__"},
                    {"allows_loop": False, "cache": True, "display_name": "Model", "group_outputs": False, "method": "build_model", "name": "model_output", "selected": "LanguageModel", "tool_mode": True, "types": ["LanguageModel"], "value": "__UNDEFINED__"},
                ],
                "pinned": False,
                "template": {
                    "_type": "Component",
                    "code": {"type": "code", "value": ""},
                    "api_key": {"type": "str", "value": "GOOGLE_API_KEY", "_input_type": "SecretStrInput", "display_name": "Google API Key", "name": "api_key", "show": True, "advanced": False, "required": True, "list": False, "dynamic": False, "info": "The Google API Key.", "title_case": False, "password": True, "multiline": False, "load_from_db": True, "trace_as_input": False, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "model_name": {"type": "str", "value": model, "_input_type": "DropdownInput", "options": ["gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-flash", "gemini-1.5-pro"], "display_name": "Model Name", "name": "model_name", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "The name of the model to use.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "max_output_tokens": {"type": "int", "value": 0, "_input_type": "IntInput", "display_name": "Max Output Tokens", "name": "max_output_tokens", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The maximum number of tokens to generate.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "temperature": {"type": "float", "value": temperature, "_input_type": "FloatInput", "display_name": "Temperature", "name": "temperature", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "The temperature to use for sampling.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "top_p": {"type": "float", "value": None, "_input_type": "FloatInput", "display_name": "Top P", "name": "top_p", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The top-p value to use for sampling.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "top_k": {"type": "int", "value": 0, "_input_type": "IntInput", "display_name": "Top K", "name": "top_k", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The top-k value to use for sampling.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "n": {"type": "int", "value": 0, "_input_type": "IntInput", "display_name": "N", "name": "n", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Number of chat completions to generate.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "stream": {"type": "bool", "value": False, "_input_type": "BoolInput", "display_name": "Stream", "name": "stream", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Stream the response.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "system_message": {"type": "str", "value": system_message, "_input_type": "MultilineInput", "display_name": "System Message", "name": "system_message", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "System message to pass to the model.", "title_case": False, "password": False, "multiline": True, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "tool_model_enabled": {"type": "bool", "value": False, "_input_type": "BoolInput", "display_name": "Enable Tool Models", "name": "tool_model_enabled", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Enable tool models.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "input_value": {"type": "str", "value": "", "_input_type": "MessageTextInput", "input_types": ["Message"], "display_name": "Input", "name": "input_value", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "The input to the model.", "title_case": False, "password": False, "multiline": True, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                },
            },
        },
    }

def openai_node(node_id: str, x: int, y: int, system_message: str, temperature: float = 0.1, model: str = "gpt-4o-mini") -> dict:
    return {
        "id": node_id,
        "type": "genericNode",
        "position": {"x": x, "y": y},
        "data": {
            "id": node_id,
            "type": "ext:openai:OpenAIModelComponent@official",
            "node": {
                "base_classes": ["Message", "LanguageModel"],
                "beta": False,
                "conditional_paths": [],
                "custom_fields": {},
                "description": "OpenAI model.",
                "display_name": "OpenAI",
                "documentation": "",
                "edited": False,
                "field_order": ["api_key", "model_name", "max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty", "n", "stream", "system_message", "input_value", "seed", "max_retries", "timeout", "api_base"],
                "frozen": False,
                "icon": "OpenAI",
                "legacy": False,
                "metadata": {},
                "minimized": True,
                "output_types": [],
                "outputs": [
                    {"allows_loop": False, "cache": True, "display_name": "Text", "group_outputs": False, "method": "text_response", "name": "text_output", "selected": "Message", "tool_mode": True, "types": ["Message"], "value": "__UNDEFINED__"},
                    {"allows_loop": False, "cache": True, "display_name": "Model", "group_outputs": False, "method": "build_model", "name": "model_output", "selected": "LanguageModel", "tool_mode": True, "types": ["LanguageModel"], "value": "__UNDEFINED__"},
                ],
                "pinned": False,
                "template": {
                    "_type": "Component",
                    "code": {"type": "code", "value": ""},
                    "api_key": {"type": "str", "value": "OPENAI_API_KEY", "_input_type": "SecretStrInput", "display_name": "OpenAI API Key", "name": "api_key", "show": True, "advanced": False, "required": True, "list": False, "dynamic": False, "info": "The OpenAI API Key.", "title_case": False, "password": True, "multiline": False, "load_from_db": True, "trace_as_input": False, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "model_name": {"type": "str", "value": model, "_input_type": "DropdownInput", "options": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"], "display_name": "Model Name", "name": "model_name", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "The name of the model to use.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "max_tokens": {"type": "int", "value": 0, "_input_type": "IntInput", "display_name": "Max Tokens", "name": "max_tokens", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "The maximum number of tokens to generate.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "temperature": {"type": "float", "value": temperature, "_input_type": "FloatInput", "display_name": "Temperature", "name": "temperature", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "The temperature to use for sampling.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "stream": {"type": "bool", "value": False, "_input_type": "BoolInput", "display_name": "Stream", "name": "stream", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "Stream the response.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "system_message": {"type": "str", "value": system_message, "_input_type": "MultilineInput", "display_name": "System Message", "name": "system_message", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "System message to pass to the model.", "title_case": False, "password": False, "multiline": True, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "input_value": {"type": "str", "value": "", "_input_type": "MessageTextInput", "input_types": ["Message"], "display_name": "Input", "name": "input_value", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "The input to the model.", "title_case": False, "password": False, "multiline": True, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                },
            },
        },
    }

def parse_json_node(node_id: str, x: int, y: int) -> dict:
    return {
        "id": node_id,
        "type": "genericNode",
        "position": {"x": x, "y": y},
        "data": {
            "id": node_id,
            "type": "ParseJSONData",
            "node": {
                "base_classes": ["Data"],
                "beta": False,
                "conditional_paths": [],
                "custom_fields": {},
                "description": "Parse JSON data.",
                "display_name": "Parse JSON",
                "documentation": "",
                "edited": False,
                "field_order": ["input_value", "query"],
                "frozen": False,
                "icon": "Braces",
                "legacy": False,
                "metadata": {},
                "minimized": True,
                "output_types": [],
                "outputs": [{
                    "allows_loop": False, "cache": True, "display_name": "Data",
                    "group_outputs": False, "method": "parse_json", "name": "parsed_data",
                    "selected": "Data", "tool_mode": True, "types": ["Data"], "value": "__UNDEFINED__",
                }],
                "pinned": False,
                "template": {
                    "_type": "Component",
                    "code": {"type": "code", "value": ""},
                    "input_value": {"type": "str", "value": "", "_input_type": "MessageTextInput", "input_types": ["Message", "Data"], "display_name": "Input", "name": "input_value", "show": True, "advanced": False, "required": False, "list": False, "dynamic": False, "info": "JSON string or data to parse.", "title_case": False, "password": False, "multiline": True, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                    "query": {"type": "str", "value": "", "_input_type": "MessageTextInput", "display_name": "Query", "name": "query", "show": True, "advanced": True, "required": False, "list": False, "dynamic": False, "info": "JQ query to filter.", "title_case": False, "password": False, "multiline": False, "load_from_db": False, "trace_as_input": True, "trace_as_metadata": True, "track_in_telemetry": False, "tool_mode": False, "list_add_label": "Add More", "placeholder": "", "fileTypes": [], "file_path": "", "api_editable": False, "override_skip": False},
                },
            },
        },
    }


def make_edge(source_id: str, source_type: str, source_name: str, source_types: list[str],
              target_id: str, target_field: str, target_input_types: list[str]) -> dict:
    sh = {"dataType": source_type, "id": source_id, "name": source_name, "output_types": source_types}
    th = {"fieldName": target_field, "id": target_id, "inputTypes": target_input_types, "type": "str" if "Message" in target_input_types else "other"}
    return {
        "id": f"reactflow__edge-{source_id}{json.dumps(sh).replace(chr(34), chr(29))}-{target_id}{json.dumps(th).replace(chr(34), chr(29))}",
        "source": source_id,
        "target": target_id,
        "sourceHandle": json.dumps(sh, separators=(",", ":")).replace('"', "\u0153"),
        "targetHandle": json.dumps(th, separators=(",", ":")).replace('"', "\u0153"),
        "data": {"sourceHandle": sh, "targetHandle": th},
        "className": "",
        "selected": False,
        "animated": False,
    }

# Simplified edge builder matching the pattern from existing flows
def edge(input_node_id: str, input_type: str, output_name: str, output_types: list[str],
         target_node_id: str, target_field: str, target_input_types: list[str]) -> dict:
    sh_data = {"dataType": input_type, "id": input_node_id, "name": output_name, "output_types": output_types}
    th_data = {"fieldName": target_field, "id": target_node_id, "inputTypes": target_input_types, "type": "str" if "Message" in target_input_types else "other"}
    # Use the exact \u0153 separator pattern from existing edges
    sep = "\u0153"
    sh_str = json.dumps(sh_data, separators=(",", ":")).replace('"', sep)
    th_str = json.dumps(th_data, separators=(",", ":")).replace('"', sep)
    return {
        "id": f"reactflow__edge-{input_node_id}{{{sep}dataType{sep}:{sep}{input_type}{sep},{sep}id{sep}:{sep}{input_node_id}{sep},{sep}name{sep}:{sep}{output_name}{sep},{sep}output_types{sep}:{json.dumps(output_types).replace(chr(34), sep)}}}-{target_node_id}{{{sep}fieldName{sep}:{sep}{target_field}{sep},{sep}id{sep}:{sep}{target_node_id}{sep},{sep}inputTypes{sep}:{json.dumps(target_input_types).replace(chr(34), sep)},{sep}type{sep}:{sep}{th_data['type']}{sep}}}",
        "source": input_node_id,
        "target": target_node_id,
        "sourceHandle": f"{{{sep}dataType{sep}:{sep}{input_type}{sep},{sep}id{sep}:{sep}{input_node_id}{sep},{sep}name{sep}:{sep}{output_name}{sep},{sep}output_types{sep}:{json.dumps(output_types).replace(chr(34), sep)}}}",
        "targetHandle": f"{{{sep}fieldName{sep}:{sep}{target_field}{sep},{sep}id{sep}:{sep}{target_node_id}{sep},{sep}inputTypes{sep}:{json.dumps(target_input_types).replace(chr(34), sep)},{sep}type{sep}:{sep}{th_data['type']}{sep}}}",
        "data": {"sourceHandle": sh_data, "targetHandle": th_data},
        "className": "",
        "selected": False,
        "animated": False,
    }


def std_llm_flow(name: str, purpose: str, system_prompt: str, model_type: str = "gemini") -> dict:
    """Standard pattern: ChatInput -> LLM -> ChatOutput"""
    inp = _uid("ChatInput", purpose)
    llm = _uid("LLM", purpose)
    out = _uid("ChatOutput", purpose)

    if model_type == "openai":
        llm_node = openai_node(llm, 500, 200, system_prompt)
    else:
        llm_node = gemini_node(llm, 500, 200, system_prompt)

    nodes = [
        chat_input_node(inp, 100, 200),
        llm_node,
        chat_output_node(out, 900, 200),
    ]
    edges = [
        edge(inp, "ChatInput", "message", ["Message"], llm, "input_value", ["Message"]),
        edge(llm, llm_node["data"]["type"], "text_output", ["Message"], out, "input_value", ["Data", "JSON", "DataFrame", "Table", "Message"]),
    ]
    return {
        "name": f"Teman Bule - {name}",
        "description": f"{purpose} flow. Blueprint ref: .blueprint/langflow-flows.md - {purpose}.",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}},
        "folder_id": PROJECT_ID,
    }


# ── Flow definitions ───────────────────────────────────────────────────────

FLOWS = {
    "practice_interaction": {
        "name": "Practice Interaction v1",
        "prompt": (
            "You are an English language tutor for Indonesian learners. "
            "The user message is a JSON object with fields: learner_text (string), "
            "agent (one of: elean, willy), category (string), conversation_history (array of turns), "
            "learner_level (string), known_vocabulary (array of strings). "
            "Generate a tutor response as a single valid JSON object (no markdown) with keys: "
            "response_text (your main reply in English, appropriate for the learner level), "
            "response_text_id (Indonesian translation of your reply), "
            "corrections (array of {original, corrected, explanation} for any mistakes, empty if none), "
            "vocabulary_highlights (array of new/notable words used with definitions), "
            "suggested_reply (a suggested follow-up the learner could say), "
            "citations (array of source references if any, empty if none). "
            "Be encouraging and constructive. Adapt complexity to the learner level. "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "learning_assistance": {
        "name": "Learning Assistance v1",
        "prompt": (
            "You are a learning assistance component for a language-learning backend. "
            "The user message is a JSON object with: question (string), "
            "content_version_id (string), content_text (string - the lesson material), "
            "content_type (one of: video, reading, grammar_exercise, quiz), "
            "learner_level (string), conversation_history (array). "
            "Provide a grounded answer as a single valid JSON object (no markdown) with keys: "
            "answer (clear explanation in English, adapted to learner level), "
            "answer_id (Indonesian translation), "
            "citations (array of {content_section, relevance} referencing the provided material), "
            "related_concepts (array of related vocabulary or grammar points), "
            "follow_up_questions (array of suggested questions the learner might ask). "
            "Only use information from the provided content. If the answer is not in the content, say so. "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "voice_note_transcription": {
        "name": "Voice Note Transcription v1",
        "prompt": (
            "You are a speech-to-text transcription component. "
            "The user message is a JSON object with: audio_url (string), "
            "language_hint (string, default: en), media_format (string). "
            "Output a single valid JSON object (no markdown) with keys: "
            "transcript (the transcribed text), "
            "confidence (0.0-1.0), "
            "language_detected (ISO 639-1 code), "
            "duration_seconds (number), "
            "segments (array of {start, end, text} for timed segments). "
            "If the audio is unclear, indicate low confidence. Output raw JSON only."
        ),
        "model": "gemini",
    },
    "learning_content_ingestion": {
        "name": "Learning Content Ingestion v1",
        "prompt": (
            "You are a content ingestion component for a language-learning backend. "
            "The user message is a JSON object with: content_version_id (string), "
            "title (string), content_type (one of: video, reading, grammar_exercise, quiz), "
            "content_text (string - the full lesson material), "
            "unit_id (string), lesson_id (string), level (string). "
            "Output a single valid JSON object (no markdown) with keys: "
            "chunks (array of {chunk_index, text, content_type, metadata: {section, difficulty, key_concepts}}), "
            "summary (2-3 sentence summary of the content), "
            "vocabulary_list (array of {word, definition, example_sentence}), "
            "grammar_points (array of {pattern, explanation, examples}). "
            "Split content into semantically coherent chunks of 200-500 tokens each. "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "agent_knowledge_ingestion": {
        "name": "Agent Knowledge Ingestion v1",
        "prompt": (
            "You are a knowledge ingestion component for an AI language tutor system. "
            "The user message is a JSON object with: knowledge_version_id (string), "
            "agent_name (one of: elean, willy), knowledge_text (string), "
            "knowledge_type (one of: persona_background, teaching_methodology, cultural_notes, common_mistakes), "
            "language (string). "
            "Output a single valid JSON object (no markdown) with keys: "
            "chunks (array of {chunk_index, text, metadata: {topic, relevance_score, persona_aspect}}), "
            "summary (brief summary of the knowledge), "
            "key_traits (array of personality or teaching traits extracted). "
            "Split knowledge into coherent chunks of 200-400 tokens. "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "podcast_document_ingestion": {
        "name": "Podcast Document Ingestion v1",
        "prompt": (
            "You are a document ingestion component for a podcast generation system. "
            "The user message is a JSON object with: document_id (string), "
            "title (string), pages (array of {page_number, text}), "
            "source_type (string, default: pdf). "
            "Output a single valid JSON object (no markdown) with keys: "
            "title_extracted (string), "
            "authors (array of strings if detectable, empty otherwise), "
            "abstract (string if detectable, empty otherwise), "
            "chunks (array of {chunk_index, text, page_refs: array of page numbers, section, key_findings}), "
            "total_pages (number), "
            "language (detected language). "
            "Split into coherent chunks preserving page references. "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "podcast_script_generation": {
        "name": "Podcast Script Generation v1",
        "prompt": (
            "You are a podcast script generation component. "
            "The user message is a JSON object with: document_title (string), "
            "chunks (array of text chunks with page_refs), "
            "persona_elean (object with personality and speaking style), "
            "persona_willy (object with personality and speaking style), "
            "target_duration_minutes (number, default: 10), "
            "language (string, default: en with Indonesian explanations). "
            "Generate a podcast script as a single valid JSON object (no markdown) with keys: "
            "title (catchy podcast episode title), "
            "outline (array of section titles), "
            "segments (array of {segment_id, speaker: one of elean|willy, text, citations: [{chunk_index, page_refs}], estimated_duration_seconds}), "
            "total_estimated_duration_seconds (number), "
            "grounding_notes (notes on how content maps to source material). "
            "Speakers must alternate naturally. All claims must cite source chunks. "
            "Elean is warm and curious; Willy is analytical and precise. "
            "Include Indonesian translations/explanations for complex terms. "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "toefl_evaluation": {
        "name": "TOEFL Evaluation v1",
        "prompt": (
            "You are a TOEFL evaluation component for a language-learning backend. "
            "The user message is a JSON object with: test_id (string), "
            "section (one of: reading, listening, writing, speaking), "
            "questions (array of {question_id, question_text, question_type, correct_answer, rubric}), "
            "answers (array of {question_id, answer_text}), "
            "rubric_version (string). "
            "Evaluate and output a single valid JSON object (no markdown) with keys: "
            "overall_score (0-120 scale estimate), "
            "section_score (0-30 for this section), "
            "question_evaluations (array of {question_id, is_correct, score, max_score, feedback, evidence}), "
            "strengths (array of strings), "
            "weaknesses (array of strings), "
            "improvement_suggestions (array of {area, suggestion, priority: high|medium|low}), "
            "estimated_cefr_level (one of: A1, A2, B1, B2, C1, C2). "
            "For objective questions, compare against correct_answer deterministically. "
            "For writing/speaking, use the provided rubric. "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "toefl_feedback_ingestion": {
        "name": "TOEFL Feedback Ingestion v1",
        "prompt": (
            "You are a feedback ingestion component for TOEFL practice results. "
            "The user message is a JSON object with: evaluation_id (string), "
            "test_id (string), section (string), scores (object), "
            "question_evaluations (array), feedback_text (string), "
            "strengths (array), weaknesses (array), suggestions (array). "
            "Output a single valid JSON object (no markdown) with keys: "
            "chunks (array of {chunk_index, text, metadata: {dimension, score_range, feedback_type}}), "
            "summary (concise performance summary), "
            "progress_markers (array of {skill, current_level, target_level, gap_analysis}). "
            "Split feedback into indexable chunks for future personalized recommendations. "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "dual_embedding_dispatch": {
        "name": "Dual Embedding Dispatch v1",
        "prompt": (
            "You are a dispatch coordination component. "
            "The user message is a JSON object with: source_id (string), "
            "source_type (one of: conversation_summary, user_fact, learning_content, agent_knowledge, toefl_feedback), "
            "content_text (string), metadata (object), "
            "embedding_profiles (array of {profile_id, provider, model, dimensions, astra_collection}). "
            "Output a single valid JSON object (no markdown) with keys: "
            "dispatch_id (generated unique ID), "
            "jobs (array of {job_id, profile_id, provider, model, status: dispatched}), "
            "source_ref (the source_id), "
            "created_at (ISO 8601 timestamp placeholder). "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "embedding_projection_gemini": {
        "name": "Embedding Projection Gemini v1",
        "prompt": (
            "You are an embedding projection component for Gemini. "
            "The user message is a JSON object with: chunk_refs (array of {chunk_id, text}), "
            "profile_id (string), model (string), dimensions (number), "
            "astra_collection (string), metadata (object). "
            "Output a single valid JSON object (no markdown) with keys: "
            "projection_id (generated unique ID), "
            "collection (astra_collection), "
            "projected_count (number of chunks processed), "
            "chunk_ids (array of processed chunk_ids), "
            "status (one of: completed, partial, failed), "
            "profile_used (profile_id). "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
    "embedding_projection_openai": {
        "name": "Embedding Projection OpenAI v1",
        "prompt": (
            "You are an embedding projection component for OpenAI. "
            "The user message is a JSON object with: chunk_refs (array of {chunk_id, text}), "
            "profile_id (string), model (string), dimensions (number), "
            "astra_collection (string), metadata (object). "
            "Output a single valid JSON object (no markdown) with keys: "
            "projection_id (generated unique ID), "
            "collection (astra_collection), "
            "projected_count (number of chunks processed), "
            "chunk_ids (array of processed chunk_ids), "
            "status (one of: completed, partial, failed), "
            "profile_used (profile_id). "
            "Output raw JSON only."
        ),
        "model": "openai",
    },
    "session_context_preparation": {
        "name": "Session Context Preparation v1",
        "prompt": (
            "You are a context preparation component for voice/video call sessions. "
            "The user message is a JSON object with: session_id (string), "
            "user_id (string), agent (one of: elean, willy), "
            "session_type (one of: voice_call, video_call, podcast_playback), "
            "learner_profile (object with level, goals, preferences), "
            "recent_interactions (array of recent conversation summaries), "
            "active_vocabulary (array of vocabulary items being learned), "
            "pending_reviews (array of review items). "
            "Output a single valid JSON object (no markdown) with keys: "
            "context_snapshot_id (generated unique ID), "
            "greeting_suggestion (personalized opening), "
            "active_topics (array of topics to potentially discuss), "
            "vocabulary_to_reinforce (array of words to naturally use), "
            "difficulty_calibration (recommended complexity level), "
            "session_goals (array of achievable goals for this session), "
            "context_window_tokens (estimated tokens needed). "
            "Output raw JSON only."
        ),
        "model": "gemini",
    },
}


def create_or_update_flow(flow_key: str, flow_def: dict) -> tuple[str, str]:
    """Create or update a flow. Returns (flow_id, action)."""
    flow_name = f"Teman Bule - {flow_def['name']}"
    
    # Check if flow already exists
    resp = httpx.get(
        f"{BASE_URL}/api/v1/flows/",
        headers=HEADERS,
        params={"project_id": PROJECT_ID},
        timeout=15,
    )
    resp.raise_for_status()
    existing = resp.json()
    
    existing_flow = None
    for f in existing:
        if f["name"] == flow_name:
            existing_flow = f
            break
    
    # Build flow payload
    flow_data = std_llm_flow(flow_def["name"], flow_key, flow_def["prompt"], flow_def.get("model", "gemini"))
    
    if existing_flow:
        # Update existing flow
        flow_id = existing_flow["id"]
        update_payload = {
            "name": flow_data["name"],
            "description": flow_data["description"],
            "data": flow_data["data"],
        }
        resp = httpx.patch(
            f"{BASE_URL}/api/v1/flows/{flow_id}",
            headers=HEADERS,
            json=update_payload,
            timeout=15,
        )
        resp.raise_for_status()
        return flow_id, "updated"
    else:
        # Create new flow
        resp = httpx.post(
            f"{BASE_URL}/api/v1/flows/",
            headers=HEADERS,
            json=flow_data,
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        return result["id"], "created"


def fix_practice_interaction() -> tuple[str, str]:
    """Fix the existing practice_interaction flow that has no edges."""
    flow_id = "35990f67-ed19-4e8b-a0df-657b748c5e8e"
    flow_def = FLOWS["practice_interaction"]
    flow_data = std_llm_flow(flow_def["name"], "practice_interaction", flow_def["prompt"], "gemini")
    
    update_payload = {
        "name": flow_data["name"],
        "description": flow_data["description"],
        "data": flow_data["data"],
    }
    resp = httpx.patch(
        f"{BASE_URL}/api/v1/flows/{flow_id}",
        headers=HEADERS,
        json=update_payload,
        timeout=15,
    )
    resp.raise_for_status()
    return flow_id, "fixed"


def main():
    results = []
    
    # 1. Fix practice_interaction (existing, broken - no edges)
    try:
        fid, action = fix_practice_interaction()
        results.append(("practice_interaction", fid, action, "OK"))
    except Exception as e:
        results.append(("practice_interaction", "", "fix", f"ERROR: {e}"))
    
    # 2. Skip existing working flows (conversation_ingestion, user_fact_extraction, learning_assessment)
    skip = {"conversation_ingestion", "user_fact_extraction", "learning_assessment"}
    
    # 3. Create/update all other flows
    for key, fdef in FLOWS.items():
        if key in skip:
            results.append((key, "", "skip", "already exists and working"))
            continue
        if key == "practice_interaction":
            continue  # already fixed above
        try:
            fid, action = create_or_update_flow(key, fdef)
            results.append((key, fid, action, "OK"))
        except Exception as e:
            results.append((key, "", "create/update", f"ERROR: {e}"))
    
    # Print summary
    print("\n" + "=" * 80)
    print(f"{'Flow Key':<35} {'Flow ID':<40} {'Action':<12} {'Status'}")
    print("=" * 80)
    for key, fid, action, status in results:
        print(f"{key:<35} {fid:<40} {action:<12} {status}")
    print("=" * 80)
    
    errors = [r for r in results if "ERROR" in r[3]]
    if errors:
        print(f"\n{len(errors)} errors occurred!")
        return 1
    
    print(f"\nAll {len(results)} flows processed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

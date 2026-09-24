import re
import os
import json
import base64
import inspect
import asyncio
import requests
import mimetypes
from typing import Any, Dict, List, Optional, Tuple

# =============================================================================
# KONFIGURASI GLOBAL CALLCRAFT (CENTRALIZED CONFIGURATION)
# Ubah konfigurasi di bawah ini jika URL, domain, timeout, atau endpoint berubah.
# Anda hanya perlu mengubah nilai di bagian ini tanpa mengedit logika di bawah.
# =============================================================================

# 1. Base URL API Callcraft Production
# Secara default mengarah langsung ke server produksi Callcraft (callcraft.flyup.id).
# Endpoint backend API berada di: https://callcraft-api.flyup.id
CALLCRAFT_BASE_URL: str = os.environ.get("CALLCRAFT_API_URL", "https://callcraft-api.flyup.id")

# 2. Endpoint Path API Callcraft
CALLCRAFT_PROJECTS_ENDPOINT: str = "/v1/projects"
CALLCRAFT_SPECS_ENDPOINT: str = "/v1/specs"
CALLCRAFT_CALL_ENDPOINT: str = "/v1/call"

# 3. Timeout Konfigurasi HTTP (dalam detik)
CALLCRAFT_API_TIMEOUT: int = 60          # Timeout eksekusi spec AI (POST /v1/call)
CALLCRAFT_DISCOVERY_TIMEOUT: int = 10    # Timeout penarikan list project/specs dropdown

# 4. Metadata Tampilan Komponen Langflow
COMPONENT_DISPLAY_NAME: str = "Callcraft Spec"
COMPONENT_DESCRIPTION: str = "Menerima objek Data/Message/JSON dari node sebelumnya, otomatis ekstrak file ke Base64, dan mengeksekusi AI Callcraft Spec."
COMPONENT_DOCUMENTATION: str = "https://callcraft.flyup.id"
COMPONENT_ICON: str = "Workflow"

# 5. Metadata Internal Langflow yang Diabaikan dari Body Request
IGNORED_METADATA_KEYS: set = {
    "timestamp", "sender", "sender_name", "session_id", "context_id",
    "error", "edit", "properties", "category", "content_blocks",
    "session_metadata", "id", "flow_id", "run_id", "duration",
    "text_key", "default_value"
}

# 6. Base URL Server Langflow untuk Resolusi File Upload Chat
# Digunakan untuk mengubah path file relatif dari chat menjadi URL lengkap.
LANGFLOW_DEFAULT_BASE_URL: str = os.environ.get("LANGFLOW_BASE_URL") or os.environ.get("LANGFLOW_URL") or "https://langflow.flyup.id"

# 7. Ekstensi & Key Khusus File untuk Validasi File
KNOWN_FILE_EXTENSIONS: set = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".svg",
    ".pdf", ".csv", ".xlsx", ".xls", ".doc", ".docx", ".txt", ".json",
    ".xml", ".zip", ".tar", ".gz", ".mp3", ".wav", ".mp4", ".mov", ".avi"
}

KNOWN_FILE_KEYS: set = {
    "image", "images", "file", "files", "pdf", "document", "documents",
    "doc", "docs", "foto", "gambar", "attachment", "attachments"
}


# =============================================================================
# IMPORTS FRAMEWORK LANGFLOW / LFX
# Langflow AST validator (validate.py) mewajibkan import top-level langsung
# tanpa blok try/except bersarang agar simbol Component dikenali saat build.
# =============================================================================
from lfx.base.models.unified_models import (
    get_api_key_for_provider,
    handle_model_input_update,
)
from lfx.base.models.watsonx_constants import IBM_WATSONX_URLS
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DictInput, DropdownInput, ModelInput, SecretStrInput, StrInput, TableInput
from lfx.io import DataInput, Output
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict
from lfx.services.deps import get_storage_service


def _clean_base_url(url: Optional[str]) -> str:
    """
    Membersihkan dan menormalisasi Base URL:
    1. Menggunakan default CALLCRAFT_BASE_URL jika kosong.
    2. Menambahkan skema https:// jika protokol belum disertakan.
    3. Mengarahkan domain frontend callcraft.flyup.id ke backend API callcraft-api.flyup.id.
    4. Menghilangkan trailing slash '/' dan path '/v1' jika pengguna menyertakannya.
    """
    raw = (url or CALLCRAFT_BASE_URL).strip()
    if not raw:
        raw = CALLCRAFT_BASE_URL

    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = f"https://{raw}"

    # Jika pengguna memasukkan domain frontend (callcraft.flyup.id), alihkan ke API server
    if "callcraft.flyup.id" in raw and "callcraft-api.flyup.id" not in raw:
        raw = raw.replace("callcraft.flyup.id", "callcraft-api.flyup.id")

    raw = raw.rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[:-3]
    return raw


def _clean_base_url_scheme(url: Optional[str], default_url: str = CALLCRAFT_BASE_URL) -> str:
    """
    Menormalisasi URL agar memiliki skema http/https dan tanpa trailing slash.
    """
    raw = (url or default_url).strip()
    if not raw:
        raw = default_url

    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = f"https://{raw}"

    return raw.rstrip("/")


def _is_complete_url(val: Any) -> bool:
    """
    Memeriksa apakah nilai sudah merupakan URL lengkap (http://, https://, atau data: URI).
    Jika sudah lengkap, nilai tidak perlu dimodifikasi ("kalau sudah lengkap maka tidak perlu").
    """
    if not isinstance(val, str):
        return False
    trimmed = val.strip().lower()
    return trimmed.startswith(("http://", "https://", "data:"))


def _is_file_path_or_reference(key: str, val: Any, known_files: Optional[List[str]] = None) -> bool:
    """
    Mendeteksi apakah suatu nilai merupakan path / referensi file.
    Kriteria:
    1. Nilai string sama dengan salah satu file yang diunggah di chat (known_files).
    2. Nilai string diawali dengan prefix path storage/file Langflow (/api/v1/files, api/v1/files, /files/).
    3. Nama key merupakan salah satu KNOWN_FILE_KEYS dan nilai adalah string tanpa spasi/newline.
    4. Nilai string berakhiran salah satu ekstensi file yang valid (KNOWN_FILE_EXTENSIONS) dan tidak mengandung spasi/newline.
    """
    if not isinstance(val, str):
        return False

    trimmed = val.strip()
    if not trimmed or "\n" in trimmed or "\r" in trimmed:
        return False

    # 1. Cek kecocokan langsung dengan file dari chat
    if known_files:
        for kf in known_files:
            if isinstance(kf, str) and trimmed == kf.strip():
                return True

    # 2. Cek prefix path file Langflow
    lowered = trimmed.lower()
    if lowered.startswith(("/api/v1/files/", "api/v1/files/", "/files/download/", "files/download/")):
        return True

    # Nilai yang mengandung spasi bukanlah path/URL file tunggal
    if " " in trimmed:
        return False

    # 3. Cek key name yang spesifik file
    if key and key.strip().lower() in KNOWN_FILE_KEYS:
        return True

    # 4. Cek ekstensi file yang valid
    clean_path = trimmed.split("?")[0].split("#")[0].lower()
    _, ext = os.path.splitext(clean_path)
    if ext and ext in KNOWN_FILE_EXTENSIONS:
        return True

    return False


def _convert_to_complete_file_url(file_ref: str, base_url: str) -> str:
    """
    Mengubah path file relatif (misal dari chat / storage Langflow) menjadi URL lengkap.
    Aturan:
    - Jika sudah lengkap (http://, https://, data:), KEMBALIKAN UTUH tanpa modifikasi.
    - Jika relatif, gabungkan dengan base_url server Langflow.
    """
    if not isinstance(file_ref, str):
        return file_ref

    trimmed = file_ref.strip()
    if not trimmed:
        return trimmed

    # Kalau sudah lengkap maka tidak perlu diubah
    if _is_complete_url(trimmed):
        return trimmed

    normalized_base = _clean_base_url_scheme(base_url, default_url=LANGFLOW_DEFAULT_BASE_URL)

    # Tangani path yang sudah mengandung /api/v1/files/download/
    if trimmed.startswith("/api/v1/files/download/"):
        return f"{normalized_base}{trimmed}"
    elif trimmed.startswith("api/v1/files/download/"):
        return f"{normalized_base}/{trimmed}"
    elif trimmed.startswith("/files/download/"):
        return f"{normalized_base}/api/v1{trimmed}"
    elif trimmed.startswith("files/download/"):
        return f"{normalized_base}/api/v1/{trimmed}"

    # Tangani path format chat Langflow: {flow_id}/{file_name} atau folder/file
    clean_path = trimmed.lstrip("/")
    return f"{normalized_base}/api/v1/files/download/{clean_path}"


class CallcraftAPIComponent(Component):
    display_name = COMPONENT_DISPLAY_NAME
    description = COMPONENT_DESCRIPTION
    documentation: str = COMPONENT_DOCUMENTATION
    icon = COMPONENT_ICON
    name = "CallcraftAPIComponent"

    inputs = [
        # 1. Kredensial Otentikasi Wajib (Wajib diisi terlebih dahulu untuk dropdown otomatis)
        StrInput(
            name="user_id",
            display_name="User ID",
            info="ID Pengguna Callcraft Anda (misal: usr_01HZX89ABCDEF1234567890XY).",
            required=True,
        ),
        StrInput(
            name="public_key",
            display_name="Public Key",
            info="Public Key API Key Callcraft Anda (misal: pk_live_...).",
            required=True,
        ),
        SecretStrInput(
            name="secret_key",
            display_name="Secret Key",
            info="Secret Key API Key Callcraft Anda (misal: call_sk_live_...).",
            required=True,
        ),

        # 2. Dropdown Dinamis: Project & Call Spec
        DropdownInput(
            name="project_id",
            display_name="Project",
            info="Pilih project Callcraft (klik tombol refresh untuk memuat otomatis daftar project). Anda juga dapat mengetik langsung.",
            options=[],
            value="",
            combobox=True,
            refresh_button=True,
            real_time_refresh=True,
        ),
        DropdownInput(
            name="spec_id",
            display_name="Call Spec ID / Slug",
            info="Pilih Callcraft Spec yang ingin dieksekusi atau ketik spec ID/slug langsung.",
            options=[],
            value="",
            combobox=True,
            refresh_button=True,
            real_time_refresh=True,
            required=True,
        ),

        # 3. Input Data dari Node Sebelumnya (Chat Input, File, Data, Message, JSON)
        DataInput(
            name="input_data",
            display_name="Input Data (Upstream)",
            info="Hubungkan node sebelumnya di sini (Chat Input, File, Data, Message, atau JSON). File dokumen/gambar/PDF akan otomatis diekstrak ke Base64.",
            input_types=["Message", "Data", "dict", "Text", "str", "list"],
            required=False,
        ),

        # 4. Form Input Payload Key-Value (Dukungan Nilai Statis & Template Parser Dinamis)
        TableInput(
            name="payload",
            display_name="Payload (Body Params)",
            info="Tambahkan parameter body REST API secara eksplisit (Key-Value). Nilai dapat berupa teks statis, URL file lengkap, Base64 Data URI, atau template parser (contoh: {text}, {prompt}, {image}, {file}, {files[0]}, {image_base64}, {files_base64[0]}). Body request POST /v1/call HANYA dibentuk dari parameter tabel ini.",
            table_schema=[
                {
                    "name": "key",
                    "display_name": "Key",
                    "type": "str",
                    "description": "Nama parameter / field body request",
                },
                {
                    "name": "value",
                    "display_name": "Value",
                    "type": "str",
                    "description": "Nilai parameter atau template parser (misal: {text}, {prompt}, {image}, {file}, {files[0]}, {image_base64})",
                },
            ],
            value=[],
            is_list=True,
            required=True,
        ),

        # 5. Base URL Callcraft & Langflow
        StrInput(
            name="base_url",
            display_name="Callcraft Base URL",
            info="Base URL API Callcraft. Secara default mengarah langsung ke server produksi.",
            value=CALLCRAFT_BASE_URL,
            required=True,
            advanced=True,
        ),
        StrInput(
            name="langflow_base_url",
            display_name="Langflow Base URL",
            info="Base URL server Langflow untuk resolusi URL lengkap file upload dari chat (misal: https://langflow.flyup.id). Default mengacu pada environment variable LANGFLOW_BASE_URL.",
            value=LANGFLOW_DEFAULT_BASE_URL,
            required=False,
            advanced=True,
        ),
        DropdownInput(
            name="file_encoding",
            display_name="Chat File Format",
            info="Format pengiriman file chat ke API Callcraft: 'Base64 Data URI' (Sangat direkomendasikan jika server Langflow bersifat privat / memerlukan autentikasi login) atau 'Complete File URL' (hanya jika server Langflow dapat diakses publik tanpa login).",
            options=["Base64 Data URI (Recommended)", "Complete File URL"],
            value="Base64 Data URI (Recommended)",
            required=False,
            advanced=True,
        ),

        # 4. Input Manual Opsional
        StrInput(
            name="custom_prompt",
            display_name="Additional Prompt",
            info="Opsional: Prompt instruksi tambahan untuk ekstraksi AI.",
            value="",
            required=False,
            advanced=True,
        ),
        StrInput(
            name="document_input",
            display_name="Document URL / Base64 (Manual)",
            info="Opsional: Masukkan URL dokumen atau data Base64 jika tidak menggunakan node input sebelumnya.",
            value="",
            required=False,
            advanced=True,
        ),

        # 5. Optional Headers: Interactive AI Model & Provider Override (X-AI-MODEL-NAME, X-AI-API-KEY, X-AI-BASE-URL)
        ModelInput(
            name="model",
            display_name="Language Model",
            info="Opsional: Pilih provider dan model AI override interaktif (misal: OpenAI, Anthropic, Gemini, Groq, Ollama). Kosongkan jika ingin menggunakan model default dari Call Spec.",
            real_time_refresh=True,
            required=False,
        ),
        SecretStrInput(
            name="api_key",
            display_name="AI API Key",
            info="Opsional: API Key provider AI override (header X-AI-API-KEY). Kosongkan jika sudah dikonfigurasi di global variables Langflow atau Callcraft dashboard.",
            real_time_refresh=True,
            advanced=True,
        ),
        DropdownInput(
            name="base_url_ibm_watsonx",
            display_name="watsonx API Endpoint",
            info="The base URL of the API (IBM watsonx.ai only)",
            options=IBM_WATSONX_URLS,
            value=IBM_WATSONX_URLS[0],
            combobox=True,
            show=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="ollama_base_url",
            display_name="Ollama API URL",
            info="Endpoint of the Ollama API (Ollama only)",
            show=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="ai_base_url",
            display_name="AI Base URL",
            info="Opsional: Base URL custom provider AI override (header X-AI-BASE-URL), misal: endpoint vLLM atau proxy custom.",
            required=False,
            advanced=True,
        ),
        StrInput(
            name="ai_model_name",
            display_name="AI Model Name Override",
            info="Opsional: Nama model AI manual override jika tidak memilih dari dropdown Language Model.",
            required=False,
            advanced=True,
        ),

    ]

    outputs = [
        Output(display_name="Output JSON", name="output_json", method="call_api"),
    ]

    def _resolve_langflow_base_url(self) -> str:
        """
        Menentukan Base URL server Langflow untuk resolusi URL file lengkap.
        Hirarki prioritas:
        1. Input 'langflow_base_url' pada komponen jika dikonfigurasi.
        2. Environment variable LANGFLOW_BASE_URL atau LANGFLOW_URL.
        3. Service settings Langflow (jika berjalan di dalam engine Langflow).
        4. Default terpusat: LANGFLOW_DEFAULT_BASE_URL.
        """
        custom_url = self._get_input_value("langflow_base_url").strip()
        if custom_url:
            return _clean_base_url_scheme(custom_url, default_url=LANGFLOW_DEFAULT_BASE_URL)

        env_url = (os.environ.get("LANGFLOW_BASE_URL") or os.environ.get("LANGFLOW_URL") or "").strip()
        if env_url:
            return _clean_base_url_scheme(env_url, default_url=LANGFLOW_DEFAULT_BASE_URL)

        try:
            from lfx.services.deps import get_settings_service
            svc = get_settings_service()
            if svc and hasattr(svc, "settings"):
                backend_url = getattr(svc.settings, "backend_url", None) or getattr(svc.settings, "base_url", None)
                if backend_url and isinstance(backend_url, str) and backend_url.strip():
                    return _clean_base_url_scheme(backend_url.strip(), default_url=LANGFLOW_DEFAULT_BASE_URL)
                host = getattr(svc.settings, "host", None)
                port = getattr(svc.settings, "port", None)
                if host and port and host not in ("0.0.0.0", "127.0.0.1", "localhost"):
                    return f"http://{host}:{port}"
        except Exception:
            pass

        return _clean_base_url_scheme(LANGFLOW_DEFAULT_BASE_URL, default_url=LANGFLOW_DEFAULT_BASE_URL)

    def _normalize_payload_files(
        self,
        data: Any,
        key: str = "",
        known_files: Optional[List[str]] = None,
        known_file_base64_map: Optional[Dict[str, str]] = None,
        langflow_base_url: str = "",
        prefer_base64: bool = True,
    ) -> Any:
        """
        Secara rekursif memeriksa apakah nilai dalam payload adalah file.
        Jika file:
          - Jika sudah lengkap (http://, https://, data:) -> biarkan utuh ("kalau sudah lengkap maka tidak perlu").
          - Jika belum lengkap (misal path relatif dari chat):
            * Jika prefer_base64=True dan file ada di known_file_base64_map -> ubah ke Base64 Data URI.
            * Lainnya -> ubah ke URL lengkap.
        Jika bukan file -> biarkan nilai asli tanpa perubahan.
        """
        if isinstance(data, dict):
            return {
                k: self._normalize_payload_files(
                    v,
                    key=k,
                    known_files=known_files,
                    known_file_base64_map=known_file_base64_map,
                    langflow_base_url=langflow_base_url,
                    prefer_base64=prefer_base64,
                )
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [
                self._normalize_payload_files(
                    item,
                    key=key,
                    known_files=known_files,
                    known_file_base64_map=known_file_base64_map,
                    langflow_base_url=langflow_base_url,
                    prefer_base64=prefer_base64,
                )
                for item in data
            ]
        elif isinstance(data, str):
            trimmed = data.strip()
            if _is_complete_url(trimmed):
                return trimmed
            if _is_file_path_or_reference(key, trimmed, known_files=known_files):
                if prefer_base64 and known_file_base64_map and trimmed in known_file_base64_map:
                    return known_file_base64_map[trimmed]
                return _convert_to_complete_file_url(trimmed, langflow_base_url)
            return data
        else:
            return data

    def _resolve_variable_value(self, val: Any) -> str:
        """
        Mendukung resolusi otomatis nilai Global Variable Langflow atau Environment Variable.
        Jika pengguna memasukkan nama variabel global seperti CALLCRAFT_USER_ID atau CALLCRAFT_SECRET_KEY,
        fungsi ini akan mengambil nilai aslinya dari Langflow Database / os.environ.
        """
        if not val:
            return ""
        if hasattr(val, "get_secret_value") and callable(val.get_secret_value):
            val = val.get_secret_value()
        elif hasattr(val, "value"):
            val = val.value
        raw = str(val).strip()
        if not raw:
            return ""

        # 1. Cek Environment Variable langsung
        if raw in os.environ:
            return os.environ[raw].strip()

        # 2. Cek Langflow Database Variable Service
        try:
            from lfx.services.deps import get_variable_service, session_scope
            from lfx.utils.async_helpers import run_until_complete
            import uuid

            async def _fetch():
                async with session_scope() as session:
                    var_svc = get_variable_service()
                    if not var_svc:
                        return None
                    u_id = getattr(self, "_user_id", None) or getattr(self, "user_id", None)
                    if not u_id:
                        return None
                    if isinstance(u_id, str):
                        try:
                            u_id = uuid.UUID(u_id)
                        except Exception:
                            return None
                    return await var_svc.get_variable(user_id=u_id, name=raw, field="", session=session)

            resolved = run_until_complete(_fetch())
            if resolved is not None:
                if hasattr(resolved, "get_secret_value"):
                    return resolved.get_secret_value()
                return str(resolved).strip()
        except Exception:
            pass

        return raw

    def _get_input_value(self, input_name: str) -> str:
        """
        Mengambil nilai mentah input dengan aman.
        Solusi kritis untuk:
        1. Langflow property shadowing: 'self.user_id' di-shadow oleh property internal Component bawaan.
           Oleh karena itu, wajib mengambil nilai dari 'self._attributes[input_name]' terlebih dahulu.
        2. Objek SecretStr: memanggil '.get_secret_value()' agar kunci rahasia tidak ter-masking menjadi '**********'.
        3. Global Variables Langflow: otomatis di-resolve nilainya dari database/env.
        """
        val = None

        if hasattr(self, "_attributes") and isinstance(self._attributes, dict) and input_name in self._attributes:
            val = self._attributes[input_name]

        if val is None:
            attr_val = getattr(self, input_name, None)
            if input_name == "user_id" and attr_val is getattr(self, "_user_id", None):
                val = None
            else:
                val = attr_val

        if val is None:
            return ""

        return self._resolve_variable_value(val)

    def _resolve_ai_model_and_provider(self) -> Tuple[str, str]:
        """
        Mengekstrak (model_name, provider) dari ModelInput atau manual override.
        """
        # 1. Manual model name override memiliki prioritas jika diisi
        manual_override = self._get_input_value("ai_model_name").strip()
        if manual_override:
            return manual_override, ""

        # 2. Ambil dari ModelInput (self.model)
        model_val = getattr(self, "model", None)
        if model_val is None and hasattr(self, "_attributes") and isinstance(self._attributes, dict):
            model_val = self._attributes.get("model")

        if isinstance(model_val, list) and model_val:
            first = model_val[0]
            if isinstance(first, dict):
                name = str(first.get("name") or "").strip()
                provider = str(first.get("provider") or "").strip()
                return name, provider
            elif isinstance(first, str):
                return first.strip(), ""
        elif isinstance(model_val, dict):
            name = str(model_val.get("name") or "").strip()
            provider = str(model_val.get("provider") or "").strip()
            return name, provider
        elif isinstance(model_val, str) and model_val.strip():
            return model_val.strip(), ""

        # Fallback jika model adalah objek BaseLanguageModel (edge yang terhubung)
        try:
            from langchain_core.language_models import BaseLanguageModel
            if isinstance(model_val, BaseLanguageModel):
                for attr in ("model_name", "model", "model_id"):
                    val = getattr(model_val, attr, None)
                    if isinstance(val, str) and val.strip():
                        return val.strip(), type(model_val).__name__
                return type(model_val).__name__, ""
        except Exception:
            pass

        return "", ""

    def _resolve_ai_api_key(self, provider: str = "") -> str:
        """
        Mengambil API Key provider dari input api_key/ai_api_key atau global variables Langflow.
        """
        key = self._get_input_value("api_key").strip() or self._get_input_value("ai_api_key").strip()
        if key:
            return key

        if provider:
            try:
                langflow_user_id = getattr(self, "user_id", None)
                resolved = get_api_key_for_provider(langflow_user_id, provider, None)
                if resolved and isinstance(resolved, str) and resolved.strip():
                    return resolved.strip()
            except Exception:
                pass

        return ""

    def _resolve_ai_base_url(self, provider: str = "") -> str:
        """
        Menentukan Base URL AI provider dari ai_base_url, ollama_base_url, atau base_url_ibm_watsonx.
        """
        custom_base = self._get_input_value("ai_base_url").strip()
        if custom_base:
            return custom_base

        if provider == "Ollama":
            ollama_url = self._get_input_value("ollama_base_url").strip()
            if ollama_url:
                return ollama_url

        if provider == "IBM WatsonX":
            watsonx_url = self._get_input_value("base_url_ibm_watsonx").strip()
            if watsonx_url:
                return watsonx_url

        return ""

    def _read_file_from_storage(self, storage_service, folder_name: str, file_name: str) -> bytes:
        """
        Membaca bytes file dari storage service Langflow dengan aman.
        Mendukung async/sync storage_service.get_file dan fallback filesystem lokal.
        """
        if storage_service:
            try:
                res = storage_service.get_file(folder_name, file_name)
                if inspect.isawaitable(res):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                    if loop and loop.is_running():
                        try:
                            import nest_asyncio
                            nest_asyncio.apply()
                            return loop.run_until_complete(res)
                        except Exception:
                            pass
                    return asyncio.run(res)
                elif isinstance(res, bytes):
                    return res
            except Exception:
                pass

            try:
                if hasattr(storage_service, "build_full_path"):
                    path_obj = storage_service.build_full_path(folder_name, file_name)
                    path_str = str(path_obj)
                    if os.path.exists(path_str):
                        with open(path_str, "rb") as f:
                            return f.read()
            except Exception:
                pass

        # Fallback pencarian file di filesystem lokal
        possible_paths = [
            os.path.join(folder_name, file_name),
            os.path.expanduser(f"~/.cache/langflow/storage/{folder_name}/{file_name}"),
            os.path.expanduser(f"~/.langflow/storage/{folder_name}/{file_name}"),
            os.path.expanduser(f"~/.cache/langflow/{folder_name}/{file_name}"),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                with open(p, "rb") as f:
                    return f.read()

        raise FileNotFoundError(f"File '{folder_name}/{file_name}' tidak dapat dibaca dari storage service maupun filesystem lokal.")

    def _extract_payload_and_files(self, input_data):
        """
        Secara otomatis mengekstrak seluruh JSON key-value dari node sebelumnya,
        sekaligus menangkap file untuk diubah ke Base64.
        """
        payload = {}
        chat_text = ""
        chat_files = []

        if not input_data:
            return payload, chat_text, chat_files

        if isinstance(input_data, (list, tuple)):
            combined_payload = {}
            all_texts = []
            all_files = []
            for item in input_data:
                p, t, f = self._extract_payload_and_files(item)
                combined_payload.update(p)
                if t:
                    all_texts.append(t)
                if f:
                    all_files.extend(f)
            seen = set()
            unique_files = [x for x in all_files if not (x in seen or seen.add(x))]
            if all_texts and "prompt" not in combined_payload:
                combined_payload["prompt"] = "\n".join(all_texts)
            return combined_payload, "\n".join(all_texts), unique_files

        raw_dict = {}
        if hasattr(input_data, "model_dump") and callable(input_data.model_dump):
            try:
                raw_dict = input_data.model_dump()
            except Exception:
                pass
        elif hasattr(input_data, "dict") and callable(input_data.dict):
            try:
                raw_dict = input_data.dict()
            except Exception:
                pass
        elif isinstance(input_data, dict):
            raw_dict = input_data
        elif isinstance(input_data, str):
            chat_text = input_data
            payload["prompt"] = chat_text
            return payload, chat_text, chat_files
        elif hasattr(input_data, "data") and isinstance(input_data.data, dict):
            raw_dict = dict(input_data.data)
        elif hasattr(input_data, "__dict__"):
            raw_dict = dict(input_data.__dict__)

        if "text" in raw_dict and isinstance(raw_dict["text"], str) and raw_dict["text"]:
            chat_text = raw_dict["text"]
        elif "content" in raw_dict and isinstance(raw_dict["content"], str) and raw_dict["content"]:
            chat_text = raw_dict["content"]

        if not chat_text:
            if hasattr(input_data, "text") and isinstance(input_data.text, str) and input_data.text:
                chat_text = input_data.text
            elif hasattr(input_data, "content") and isinstance(input_data.content, str) and input_data.content:
                chat_text = input_data.content

        if "files" in raw_dict and isinstance(raw_dict["files"], list):
            chat_files = raw_dict["files"]
        elif hasattr(input_data, "files") and isinstance(input_data.files, list):
            chat_files = input_data.files

        data_inner = raw_dict.get("data")
        if isinstance(data_inner, dict):
            if not chat_text and "text" in data_inner and isinstance(data_inner["text"], str):
                chat_text = data_inner["text"]
            if not chat_text and "content" in data_inner and isinstance(data_inner["content"], str):
                chat_text = data_inner["content"]
            if not chat_files and "files" in data_inner and isinstance(data_inner["files"], list):
                chat_files = data_inner["files"]

            for k, v in data_inner.items():
                if k not in IGNORED_METADATA_KEYS and k != "files":
                    payload[k] = v

        for k, v in raw_dict.items():
            if k not in IGNORED_METADATA_KEYS and k != "files" and k != "data":
                payload[k] = v

        if chat_text:
            if "prompt" not in payload:
                payload["prompt"] = chat_text
            if "text" in payload and "prompt" in payload:
                del payload["text"]

        seen = set()
        unique_files = [x for x in chat_files if not (x in seen or seen.add(x))]
        return payload, chat_text, unique_files

    def update_build_config(self, build_config: dict, field_value: Any, field_name: Optional[str] = None) -> dict:
        """
        1. Menghandle update ModelInput secara interaktif (refresh options, show/hide API key & base URL sesuai provider).
        2. Mengisi opsi dropdown Project dan Call Spec secara dinamis saat user memasukkan kredensial Callcraft.
        3. Memastikan konfigurasi secret_key (load_from_db, value) tidak pernah terhapus atau dirusak saat field lain diubah.
        """
        # 1. Lindungi konfigurasi dan nilai secret_key, user_id, public_key, dan project_id secara utuh
        orig_secret_key = dict(build_config["secret_key"]) if "secret_key" in build_config and isinstance(build_config["secret_key"], dict) else None
        orig_user_id = dict(build_config["user_id"]) if "user_id" in build_config and isinstance(build_config["user_id"], dict) else None
        orig_public_key = dict(build_config["public_key"]) if "public_key" in build_config and isinstance(build_config["public_key"], dict) else None
        orig_project_id = dict(build_config["project_id"]) if "project_id" in build_config and isinstance(build_config["project_id"], dict) else None

        # Panggil handle_model_input_update HANYA jika field yang diubah berkaitan dengan model/provider
        if field_name in ("model", None) or (isinstance(field_name, str) and field_name.startswith("base_url_")):
            try:
                build_config = handle_model_input_update(
                    component=self,
                    build_config=dict(build_config),
                    field_value=field_value,
                    field_name=field_name,
                    model_field_name="model",
                )
            except Exception:
                pass

        # Pulihkan konfigurasi kredensial secara utuh tanpa pernah mengubah load_from_db
        if orig_secret_key is not None and "secret_key" in build_config:
            build_config["secret_key"] = orig_secret_key

        if orig_user_id is not None and "user_id" in build_config:
            build_config["user_id"] = orig_user_id

        if orig_public_key is not None and "public_key" in build_config:
            build_config["public_key"] = orig_public_key

        if orig_project_id is not None and "project_id" in build_config:
            # Pertahankan show dan options asli Callcraft jika ada provider yang mencoba menyembunyikannya
            build_config["project_id"]["show"] = orig_project_id.get("show", True)
            if orig_project_id.get("options") and not build_config["project_id"].get("options"):
                build_config["project_id"]["options"] = orig_project_id.get("options", [])

        # 2. Ambil daftar project & spec dari API Callcraft jika kredensial sudah ada
        try:
            base_url = _clean_base_url(
                self._resolve_variable_value(build_config.get("base_url", {}).get("value") or self._get_input_value("base_url"))
            )
            user_id = self._resolve_variable_value(
                build_config.get("user_id", {}).get("value") or self._get_input_value("user_id")
            ).strip()
            public_key = self._resolve_variable_value(
                build_config.get("public_key", {}).get("value") or self._get_input_value("public_key")
            ).strip()
            secret_key = self._resolve_variable_value(
                build_config.get("secret_key", {}).get("value") or self._get_input_value("secret_key")
            ).strip()

            if not (user_id and public_key and secret_key):
                # Bersihkan dynamic spec_field leftover jika ada
                for old_k in list(build_config.keys()):
                    if old_k.startswith("spec_field_") or old_k == "payload_variables":
                        del build_config[old_k]
                return dotdict({k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in build_config.items()})

            headers = {
                "X-USER-ID": user_id,
                "X-CALL-PUBLIC-KEY": public_key,
                "Authorization": f"Bearer {secret_key}",
            }

            # Ambil daftar project
            resp_p = requests.get(
                f"{base_url}{CALLCRAFT_PROJECTS_ENDPOINT}",
                headers=headers,
                timeout=CALLCRAFT_DISCOVERY_TIMEOUT,
            )

            project_options = []
            project_map = {}
            if resp_p.status_code == 200:
                p_data = resp_p.json().get("data", [])
                for p in p_data:
                    name = p.get("name") or p.get("id")
                    pid = p.get("id")
                    project_options.append(name)
                    project_map[name] = pid

            if "project_id" in build_config:
                build_config["project_id"]["options"] = project_options

            # Ambil daftar spec sesuai project yang dipilih
            selected_proj_name = build_config.get("project_id", {}).get("value")
            target_proj_id = project_map.get(selected_proj_name, selected_proj_name)

            params = {}
            if target_proj_id:
                params["projectId"] = target_proj_id

            resp_s = requests.get(
                f"{base_url}{CALLCRAFT_SPECS_ENDPOINT}",
                headers=headers,
                params=params,
                timeout=CALLCRAFT_DISCOVERY_TIMEOUT,
            )

            spec_options = []
            s_data = []
            if resp_s.status_code == 200:
                raw_data = resp_s.json().get("data", [])
                s_data = raw_data if isinstance(raw_data, list) else ([raw_data] if isinstance(raw_data, dict) else [])
                for s in s_data:
                    if isinstance(s, dict):
                        spec_id = s.get("slug") or s.get("name") or s.get("id")
                        if spec_id:
                            spec_options.append(spec_id)

            if "spec_id" in build_config:
                build_config["spec_id"]["options"] = spec_options

            # 3. Ambil detail spec terpilih untuk mengisi key form Payload secara otomatis
            selected_spec = (
                field_value if field_name == "spec_id" and field_value
                else build_config.get("spec_id", {}).get("value")
            )
            if isinstance(selected_spec, list) and selected_spec:
                selected_spec = selected_spec[0]
            selected_spec = str(selected_spec or "").strip()

            selected_spec_obj = None
            if selected_spec:
                try:
                    resp_detail = requests.get(
                        f"{base_url}{CALLCRAFT_SPECS_ENDPOINT}",
                        headers=headers,
                        params={"specId": selected_spec},
                        timeout=CALLCRAFT_DISCOVERY_TIMEOUT,
                    )
                    if resp_detail.status_code == 200:
                        detail_json = resp_detail.json()
                        if isinstance(detail_json, dict):
                            raw_spec_obj = detail_json.get("data")
                            if isinstance(raw_spec_obj, list) and raw_spec_obj:
                                selected_spec_obj = raw_spec_obj[0]
                            elif isinstance(raw_spec_obj, dict):
                                selected_spec_obj = raw_spec_obj
                            else:
                                selected_spec_obj = detail_json
                except Exception:
                    pass

                # Fallback ke list s_data jika resp_detail gagal
                if not selected_spec_obj and s_data:
                    for s in s_data:
                        if isinstance(s, dict) and selected_spec in (s.get("slug"), s.get("id"), s.get("name")):
                            selected_spec_obj = s
                            break

            if selected_spec_obj and isinstance(selected_spec_obj, dict):
                p_vars = selected_spec_obj.get("promptVariables") or []
                if not p_vars:
                    prompts = [
                        selected_spec_obj.get("positivePrompt"),
                        selected_spec_obj.get("negativePrompt"),
                        selected_spec_obj.get("additionalPrompt"),
                    ]
                    for p in prompts:
                        if p and isinstance(p, str):
                            p_vars.extend(re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", p))

                req_schema = selected_spec_obj.get("requestSchema")
                if isinstance(req_schema, str):
                    try:
                        req_schema = json.loads(req_schema)
                    except Exception:
                        req_schema = {}

                req_props = []
                if isinstance(req_schema, dict) and "properties" in req_schema and isinstance(req_schema["properties"], dict):
                    req_props = list(req_schema["properties"].keys())

                seen = set()
                ignored = {"custom_prompt", "document_input"}
                spec_fields = []
                for f in list(p_vars) + list(req_props):
                    if f and f not in seen and f not in ignored:
                        seen.add(f)
                        spec_fields.append(f)

                # Sisipkan field bawaan Call Spec ke dalam 'payload' tanpa menghapus field manual pengguna
                if "payload" in build_config and isinstance(build_config["payload"], dict):
                    curr_payload = build_config["payload"].get("value")
                    existing_rows = []
                    existing_keys = set()

                    if isinstance(curr_payload, list):
                        for item in curr_payload:
                            if isinstance(item, dict):
                                k = str(item.get("key") or "").strip()
                                v = item.get("value", "")
                                if k:
                                    existing_rows.append({"key": k, "value": v})
                                    existing_keys.add(k)
                            elif hasattr(item, "data") and isinstance(item.data, dict):
                                k = str(item.data.get("key") or "").strip()
                                v = item.data.get("value", "")
                                if k:
                                    existing_rows.append({"key": k, "value": v})
                                    existing_keys.add(k)
                    elif isinstance(curr_payload, dict):
                        for k, v in curr_payload.items():
                            k_str = str(k).strip()
                            if k_str and k_str not in IGNORED_METADATA_KEYS and k_str != "files":
                                existing_rows.append({"key": k_str, "value": v})
                                existing_keys.add(k_str)

                    # Tambahkan field bawaan spec yang belum ada di input manual pengguna
                    for var_name in spec_fields:
                        if var_name not in existing_keys:
                            if var_name in ("image", "file", "pdf"):
                                suggested_val = "{file}"
                            elif var_name in ("prompt", "text"):
                                suggested_val = "{text}"
                            else:
                                suggested_val = f"{{{var_name}}}"
                            existing_rows.append({"key": var_name, "value": suggested_val})
                            existing_keys.add(var_name)

                    build_config["payload"]["value"] = existing_rows

            # Bersihkan dynamic spec_field leftover atau payload_variables dari build_config jika ada
            for old_k in list(build_config.keys()):
                if old_k.startswith("spec_field_") or old_k == "payload_variables":
                    del build_config[old_k]

        except Exception:
            pass

        return dotdict({k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in build_config.items()})

    def call_api(self) -> Data:
        """
        Mengeksekusi Callcraft Spec melalui POST /v1/call.
        Menggabungkan payload dari node sebelumnya, file dari storage/chat, form dinamis, dan parameter manual.
        """
        base_url = _clean_base_url(self._get_input_value("base_url"))
        url = f"{base_url}{CALLCRAFT_CALL_ENDPOINT}"

        # 1. Kredensial & Identifikasi Spec
        user_id = self._get_input_value("user_id").strip()
        spec_id = self._get_input_value("spec_id").strip()
        public_key = self._get_input_value("public_key").strip()
        secret_key = self._get_input_value("secret_key").strip()

        if not user_id:
            error_data = {
                "error": {
                    "code": "MISSING_USER_ID",
                    "message": "Field 'user_id' wajib diisi.",
                }
            }
            self.status = error_data
            return Data(data=error_data)

        if not public_key or not secret_key:
            error_data = {
                "error": {
                    "code": "MISSING_CREDENTIALS",
                    "message": "Field 'public_key' dan 'secret_key' wajib diisi.",
                }
            }
            self.status = error_data
            return Data(data=error_data)

        if not spec_id:
            error_data = {
                "error": {
                    "code": "MISSING_SPEC_ID",
                    "message": "Field 'spec_id' (Call Spec) wajib dipilih atau diisi.",
                }
            }
            self.status = error_data
            return Data(data=error_data)

        headers = {
            "Content-Type": "application/json",
            "X-USER-ID": user_id,
            "X-CALL-SPEC-ID": spec_id,
            "X-CALL-PUBLIC-KEY": public_key,
            "Authorization": f"Bearer {secret_key}",
        }

        # 2. Optional AI Model Headers (X-AI-MODEL-NAME, X-AI-API-KEY, X-AI-BASE-URL)
        ai_model_name, ai_provider = self._resolve_ai_model_and_provider()
        ai_api_key = self._resolve_ai_api_key(ai_provider)
        ai_base_url = self._resolve_ai_base_url(ai_provider)

        optional_headers_sent = False
        if ai_model_name:
            headers["X-AI-MODEL-NAME"] = ai_model_name
            optional_headers_sent = True

        if ai_api_key:
            headers["X-AI-API-KEY"] = ai_api_key
            optional_headers_sent = True

        if ai_base_url:
            headers["X-AI-BASE-URL"] = ai_base_url
            optional_headers_sent = True

        debug_trace = {
            "step1Credentials": {
                "baseUrl": base_url,
                "userId": user_id,
                "specId": spec_id,
                "publicKey": public_key,
                "secretKeyStatus": f"OK (Length: {len(secret_key)})" if secret_key else "EMPTY",
                "optionalAiHeadersSent": optional_headers_sent,
                "aiModelName": ai_model_name or None,
                "aiProvider": ai_provider or None,
                "aiApiKeySent": bool(ai_api_key),
                "aiBaseUrl": ai_base_url or None,
            },
            "step2IncomingPayload": {},
            "step3FileProcessing": [],
            "step4FinalPayloadSummary": {},
            "step5ApiResponse": {},
        }

        # 3. Ekstraksi Payload Input (Upstream Input Data & Manual Payload Body Params)
        target_input = None
        if hasattr(self, "_attributes") and isinstance(self._attributes, dict) and "input_data" in self._attributes:
            target_input = self._attributes["input_data"]
        if target_input is None:
            target_input = getattr(self, "input_data", None)
        if target_input is None and hasattr(self, "_inputs") and isinstance(self._inputs, dict) and "input_data" in self._inputs:
            target_input = getattr(self._inputs["input_data"], "value", None)

        # Ekstrak data dari node sebelumnya (Chat Input, File, Data, Message, dict)
        extracted_dict, chat_text, chat_files = self._extract_payload_and_files(target_input)

        # Resolusi Base URL Server Langflow untuk URL File Lengkap
        langflow_base_url = self._resolve_langflow_base_url()

        # Format pengiriman file (Base64 vs Complete URL)
        file_encoding_setting = self._get_input_value("file_encoding") or "Base64 Data URI (Recommended)"
        prefer_base64 = "base64" in file_encoding_setting.lower()

        # Konversi file dari chat menjadi URL lengkap jika belum lengkap
        chat_file_urls: List[str] = []
        for cf in chat_files:
            if isinstance(cf, str) and cf.strip():
                chat_file_urls.append(_convert_to_complete_file_url(cf.strip(), langflow_base_url))

        # Proses File dari Storage Langflow (Chat Attachment) untuk Base64
        base64_images: List[str] = []
        file_base64_map: Dict[str, str] = {}
        if chat_files:
            try:
                storage_service = get_storage_service()
            except Exception:
                storage_service = None

            for file_path in chat_files:
                file_step_log = {"filePath": file_path}
                if isinstance(file_path, str) and "/" in file_path:
                    folder_name, file_name = file_path.split("/", 1)
                    file_step_log["folderName"] = folder_name
                    file_step_log["fileName"] = file_name

                    try:
                        file_bytes = self._read_file_from_storage(storage_service, folder_name, file_name)
                        mime_type, _ = mimetypes.guess_type(file_name)
                        if not mime_type:
                            mime_type = "application/octet-stream"

                        b64_encoded = base64.b64encode(file_bytes).decode("utf-8")
                        data_uri = f"data:{mime_type};base64,{b64_encoded}"
                        base64_images.append(data_uri)
                        file_base64_map[file_path] = data_uri

                        file_step_log["status"] = "SUCCESS"
                        file_step_log["bytesSize"] = len(file_bytes)
                        file_step_log["mimeType"] = mime_type
                        file_step_log["resolvedUrl"] = _convert_to_complete_file_url(file_path, langflow_base_url)
                        file_step_log["resolvedBase64Prefix"] = data_uri[:35] + "..."
                    except Exception as e:
                        file_step_log["status"] = "FAILED"
                        file_step_log["error"] = str(e)

                debug_trace["step3FileProcessing"].append(file_step_log)

        document_input = self._get_input_value("document_input").strip()
        custom_prompt = self._get_input_value("custom_prompt").strip()

        # Siapkan source dictionary untuk template formatting (seperti di ParserComponent Langflow)
        source_dict = dict(extracted_dict)

        # Simpan struktur object asli jika tersedia (untuk path seperti message.data.text, message.files[0], dsb)
        if isinstance(target_input, dict):
            for tk, tv in target_input.items():
                if tk not in source_dict:
                    source_dict[tk] = tv
        elif hasattr(target_input, "model_dump") and callable(target_input.model_dump):
            try:
                for tk, tv in target_input.model_dump().items():
                    if tk not in source_dict:
                        source_dict[tk] = tv
            except Exception:
                pass
        elif hasattr(target_input, "data") and isinstance(target_input.data, dict):
            source_dict["data"] = dict(target_input.data)

        if chat_text:
            source_dict["text"] = chat_text
            source_dict["prompt"] = chat_text
            source_dict["input"] = chat_text
            if "message" not in source_dict or not isinstance(source_dict["message"], dict):
                source_dict["message"] = chat_text

        # Field file pada source_dict:
        # Tentukan representasi default untuk {file}, {image}, {files[i]}, {images[i]} berdasarkan prefer_base64
        resolved_primary_files = base64_images if (prefer_base64 and base64_images) else chat_file_urls

        if resolved_primary_files:
            source_dict["file"] = resolved_primary_files[0]
            source_dict["image"] = resolved_primary_files[0]
            source_dict["files"] = resolved_primary_files
            source_dict["images"] = resolved_primary_files
            for i, f_val in enumerate(resolved_primary_files):
                source_dict[f"files[{i}]"] = f_val
                source_dict[f"images[{i}]"] = f_val
                source_dict[f"files.{i}"] = f_val
                source_dict[f"images.{i}"] = f_val

        # Selalu sediakan token URL eksplisit
        if chat_file_urls:
            source_dict["file_url"] = chat_file_urls[0]
            source_dict["image_url"] = chat_file_urls[0]
            source_dict["files_url"] = chat_file_urls
            source_dict["images_url"] = chat_file_urls
            for i, f_url in enumerate(chat_file_urls):
                source_dict[f"files_url[{i}]"] = f_url
                source_dict[f"images_url[{i}]"] = f_url
                source_dict[f"files_url.{i}"] = f_url
                source_dict[f"images_url.{i}"] = f_url

        # Selalu sediakan token Base64 eksplisit
        if base64_images:
            source_dict["file_base64"] = base64_images[0]
            source_dict["image_base64"] = base64_images[0]
            source_dict["base64"] = base64_images[0]
            source_dict["files_base64"] = base64_images
            source_dict["images_base64"] = base64_images
            for i, b64_val in enumerate(base64_images):
                source_dict[f"files_base64[{i}]"] = b64_val
                source_dict[f"images_base64[{i}]"] = b64_val
                source_dict[f"files_base64.{i}"] = b64_val
                source_dict[f"images_base64.{i}"] = b64_val

        if custom_prompt:
            source_dict["custom_prompt"] = custom_prompt

        if document_input:
            resolved_doc = _convert_to_complete_file_url(document_input, langflow_base_url)
            source_dict["document_input"] = resolved_doc
            if "file" not in source_dict:
                source_dict["file"] = resolved_doc
            if "image" not in source_dict:
                source_dict["image"] = resolved_doc

        class _DefaultDotDict(dict):
            """
            Dictionary pembungkus yang mendukung dot-notation ({meta.order_code})
            dan graceful fallback nilai kosong jika placeholder tidak ditemukan.
            """
            def __init__(self, data_map: dict):
                super().__init__()
                for k, v in data_map.items():
                    if isinstance(v, dict):
                        self[k] = _DefaultDotDict(v)
                    else:
                        self[k] = v

            def __getattr__(self, key: str) -> Any:
                val = self.get(key)
                return "" if val is None else val

            def __missing__(self, key: str) -> str:
                return ""

        formatted_source = _DefaultDotDict(source_dict)

        # Ambil field manual dari self.payload (TableInput list of dicts, DictInput, atau JSON string)
        target_payload = None
        if hasattr(self, "_attributes") and isinstance(self._attributes, dict) and "payload" in self._attributes:
            target_payload = self._attributes["payload"]
        if target_payload is None:
            target_payload = getattr(self, "payload", None)
        if target_payload is None and hasattr(self, "_inputs") and isinstance(self._inputs, dict) and "payload" in self._inputs:
            target_payload = getattr(self._inputs["payload"], "value", None)

        manual_params: Dict[str, Any] = {}
        if isinstance(target_payload, list):
            for row in target_payload:
                if isinstance(row, dict):
                    if "key" in row and "value" in row:
                        k = str(row.get("key") or "").strip()
                        v = row.get("value", "")
                        if k and k not in IGNORED_METADATA_KEYS and k != "files":
                            manual_params[k] = v
                    else:
                        for rk, rv in row.items():
                            k_str = str(rk).strip()
                            if k_str and k_str not in IGNORED_METADATA_KEYS and k_str != "files":
                                manual_params[k_str] = rv
                elif hasattr(row, "data") and isinstance(row.data, dict):
                    if "key" in row.data and "value" in row.data:
                        k = str(row.data.get("key") or "").strip()
                        v = row.data.get("value", "")
                        if k and k not in IGNORED_METADATA_KEYS and k != "files":
                            manual_params[k] = v
                    else:
                        for rk, rv in row.data.items():
                            k_str = str(rk).strip()
                            if k_str and k_str not in IGNORED_METADATA_KEYS and k_str != "files":
                                manual_params[k_str] = rv
        elif isinstance(target_payload, dict):
            for k, v in target_payload.items():
                k_str = str(k).strip()
                if k_str and k_str not in IGNORED_METADATA_KEYS and k_str != "files":
                    manual_params[k_str] = v
        elif isinstance(target_payload, str) and target_payload.strip():
            try:
                parsed_json = json.loads(target_payload)
                if isinstance(parsed_json, dict):
                    for k, v in parsed_json.items():
                        k_str = str(k).strip()
                        if k_str and k_str not in IGNORED_METADATA_KEYS and k_str != "files":
                            manual_params[k_str] = v
                elif isinstance(parsed_json, list):
                    for row in parsed_json:
                        if isinstance(row, dict):
                            if "key" in row and "value" in row:
                                k = str(row.get("key") or "").strip()
                                v = row.get("value", "")
                                if k and k not in IGNORED_METADATA_KEYS and k != "files":
                                    manual_params[k] = v
                            else:
                                for rk, rv in row.items():
                                    k_str = str(rk).strip()
                                    if k_str and k_str not in IGNORED_METADATA_KEYS and k_str != "files":
                                        manual_params[k_str] = rv
            except Exception:
                pass
        elif hasattr(target_payload, "data") and isinstance(target_payload.data, dict):
            for k, v in target_payload.data.items():
                k_str = str(k).strip()
                if k_str and k_str not in IGNORED_METADATA_KEYS and k_str != "files":
                    manual_params[k_str] = v
        elif target_payload is not None:
            p_dict, p_text, p_files = self._extract_payload_and_files(target_payload)
            manual_params.update(p_dict)
            if not chat_text and p_text:
                chat_text = p_text
            if p_files:
                chat_files.extend(p_files)

        # STRICT FAIL-FAST VALIDATION:
        # Tidak boleh ada fallback ke upstream input_data atau akal-akalan.
        # Body request POST /v1/call HARUS bersumber langsung dari parameter Table 'payload'.
        if not manual_params:
            error_data = {
                "error": {
                    "code": "MISSING_PAYLOAD_CONFIGURATION",
                    "message": "Konfigurasi parameter body pada Table 'payload' tidak boleh kosong. Body request POST /v1/call harus ditentukan dari tabel.",
                },
                "_debugTrace": debug_trace,
            }
            self.status = error_data
            return Data(data=error_data)

        # Parse nilai form manual: jika mengandung format template {variable}, ganti dengan data dari context
        parsed_manual = {}
        for k, v in manual_params.items():
            if isinstance(v, str):
                v_trimmed = v.strip()
                # Jika persis satu placeholder sederhana {nama_variabel} tanpa spasi/teks lain
                if v_trimmed.startswith("{") and v_trimmed.endswith("}") and "{" not in v_trimmed[1:-1] and "}" not in v_trimmed[1:-1]:
                    var_token = v_trimmed[1:-1].strip()
                    # 1. Cek kecocokan langsung di source_dict (misal: {text}, {prompt}, {file}, {image}, {files[0]})
                    if var_token in source_dict:
                        formatted_val = source_dict[var_token]
                    # 2. Cek apakah mengandung dot notation atau array indexing (misal: {files[0]}, {message.files[0]}, {data.text})
                    elif "." in var_token or "[" in var_token:
                        try:
                            res = v.format_map(formatted_source)
                            if res != "":
                                formatted_val = res
                            else:
                                formatted_val = ""
                        except Exception:
                            formatted_val = ""

                        # Fallback traversal hierarkis jika format_map belum menghasilkan nilai
                        if not formatted_val:
                            parts = re.split(r"\.|\[(\d+)\]", var_token)
                            parts = [p for p in parts if p is not None and p != ""]
                            curr: Any = source_dict
                            found = True
                            for p in parts:
                                if isinstance(curr, dict) and p in curr:
                                    curr = curr[p]
                                elif isinstance(curr, (list, tuple)) and p.isdigit():
                                    idx = int(p)
                                    if 0 <= idx < len(curr):
                                        curr = curr[idx]
                                    else:
                                        found = False
                                        break
                                else:
                                    found = False
                                    break
                            if found and curr is not None:
                                formatted_val = curr
                    else:
                        formatted_val = ""
                elif "{" in v and "}" in v:
                    try:
                        formatted_val = v.format_map(formatted_source)
                    except Exception:
                        formatted_val = v
                else:
                    # Nilai statis langsung dari tabel
                    formatted_val = v
                    if v_trimmed in ("true", "false", "null"):
                        try:
                            formatted_val = json.loads(v_trimmed)
                        except Exception:
                            formatted_val = v
                    elif v_trimmed.startswith(("{", "[")) and v_trimmed.endswith(("}", "]")):
                        try:
                            formatted_val = json.loads(v_trimmed)
                        except Exception:
                            formatted_val = v
            else:
                formatted_val = v

            parsed_manual[k] = formatted_val

        # Body request POST /v1/call HANYA dan STRICT berasal dari tabel payload pengguna
        final_payload = dict(parsed_manual)

        # Pengecekan file: Jika ada field berisi file (dari chat / path relatif):
        # - Jika prefer_base64=True (default), ubah ke Base64 Data URI agar tidak tergantung pada akses publik URL Langflow.
        # - Jika prefer_base64=False, ubah ke URL lengkap.
        # - Kalau sudah lengkap (http://, https://, data:), maka tidak perlu diubah.
        final_payload = self._normalize_payload_files(
            final_payload,
            known_files=chat_files,
            known_file_base64_map=file_base64_map,
            langflow_base_url=langflow_base_url,
            prefer_base64=prefer_base64,
        )

        payload = final_payload

        debug_trace["step2IncomingPayload"] = {
            "upstreamInputPresent": bool(target_input is not None),
            "manualParamsCount": len(manual_params),
            "manualParamKeys": list(manual_params.keys()),
            "extractedText": chat_text,
            "extractedFiles": chat_files,
            "resolvedPayloadKeys": list(payload.keys()),
            "rawPayloadType": str(type(target_payload)),
        }

        debug_trace["step4FinalPayloadSummary"] = {
            "payloadKeys": list(payload.keys()),
            "fileFormat": file_encoding_setting,
            "chatFileBase64Count": len(base64_images),
            "chatFileUrls": chat_file_urls,
            "langflowBaseUrl": langflow_base_url,
        }

        # 7. Eksekusi Request ke API Callcraft
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=CALLCRAFT_API_TIMEOUT,
            )

            try:
                response_json = response.json()
            except Exception:
                response_json = {
                    "statusCode": response.status_code,
                    "text": response.text,
                }

            debug_trace["step5ApiResponse"] = {
                "httpStatus": response.status_code,
                "responseKeys": list(response_json.keys()) if isinstance(response_json, dict) else [],
            }

            self.status = debug_trace

            if isinstance(response_json, dict):
                merged_result = dict(response_json)
                merged_result["_debugTrace"] = debug_trace
                return Data(data=merged_result)

            return Data(data={"result": response_json, "_debugTrace": debug_trace})

        except requests.exceptions.RequestException as e:
            error_data = {
                "error": {
                    "code": "REQUEST_FAILED",
                    "message": str(e),
                },
                "_debugTrace": debug_trace,
            }
            self.status = error_data
            return Data(data=error_data)

from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import re
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from lfx.custom import Component
from lfx.io import (
    BoolInput,
    DataInput,
    HandleInput,
    IntInput,
    Output,
    StrInput,
    SecretStrInput,
)
from lfx.schema import Data


class MinIOUpload(Component):
    """
    Upload Base64-encoded files to an S3-compatible MinIO server.
    """

    display_name = "MinIO Upload"
    description = (
        "Upload Base64-encoded files from another Langflow component "
        "to MinIO/S3-compatible storage."
    )
    documentation = "https://min.io/docs/minio/linux/developers/python/API.html"
    icon = "Upload"
    name = "minio_upload"

    inputs = [
        HandleInput(
            name="file_input",
            display_name="Base64 File",
            info="Base64 file data from another component. Accepts Data, JSON, or Message.",
            input_types=["Data", "JSON", "Message"],
            required=True,
        ),
        StrInput(
            name="filename",
            display_name="Filename",
            info="Filename to use when uploading the file.",
            input_types=["Text", "Message"],
            required=False,
            value="",
        ),
        StrInput(
            name="content_type",
            display_name="Content Type",
            info="Optional MIME type such as application/pdf, audio/mpeg.",
            input_types=["Text", "Message"],
            required=False,
            value="",
        ),
        StrInput(
            name="bucket",
            display_name="Bucket",
            info="MinIO bucket. Defaults to MINIO_BUCKET.",
            input_types=["Text", "Message"],
            required=False,
            value="",
        ),
        StrInput(
            name="object_prefix",
            display_name="Object Prefix",
            info="Optional folder/prefix inside the bucket.",
            input_types=["Text", "Message"],
            required=False,
            value="",
        ),
        StrInput(
            name="object_name",
            display_name="Object Name",
            info="Optional final object name. If empty, Filename is used.",
            input_types=["Text", "Message"],
            required=False,
            value="",
        ),
        BoolInput(
            name="overwrite",
            display_name="Overwrite Existing",
            info="If enabled, MinIO replaces the object.",
            value=True,
        ),
        BoolInput(
            name="generate_presigned_url",
            display_name="Generate Presigned URL",
            info="Generate a temporary GET URL for the uploaded object.",
            value=False,
        ),
        IntInput(
            name="presigned_url_expiry",
            display_name="Presigned URL Expiry (seconds)",
            info="Lifetime of generated presigned URLs.",
            value=3600,
            advanced=True,
        ),
        DataInput(
            name="metadata",
            display_name="Metadata",
            info='Optional metadata as Langflow Data.',
            required=False,
            advanced=True,
        ),
        StrInput(
            name="endpoint",
            display_name="Endpoint",
            info="Optional MinIO endpoint override.",
            required=False,
            value="",
            advanced=True,
        ),
        SecretStrInput(
            name="access_key",
            display_name="Access Key",
            info="Optional override. Defaults to MINIO_ACCESS_KEY.",
            required=False,
            value="",
            advanced=True,
        ),
        SecretStrInput(
            name="secret_key",
            display_name="Secret Key",
            info="Optional override. Defaults to MINIO_SECRET_KEY.",
            required=False,
            value="",
            advanced=True,
        ),
        BoolInput(
            name="secure",
            display_name="Use HTTPS",
            info="Use TLS/HTTPS when connecting to MinIO.",
            value=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="result",
            display_name="Upload Result",
            method="upload_file",
        ),
    ]

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _env_or_input(self, value: str, env_name: str) -> str:
        return (value or os.getenv(env_name, "")).strip()

    @staticmethod
    def _extract_string(value: Any) -> str:
        """
        Safely extract string from Langflow Message, Data, or Text objects.
        """
        if not value:
            return ""
        if isinstance(value, str):
            return value
        # Handle Message objects specifically
        text_attr = getattr(value, "text", None)
        if isinstance(text_attr, str):
            return text_attr
        if isinstance(value, dict) and "text" in value:
            return str(value["text"])
        return str(value)

    # ------------------------------------------------------------------
    # MinIO
    # ------------------------------------------------------------------

    def _get_client(self):
        try:
            from minio import Minio
        except ImportError as exc:
            raise ImportError(
                "The 'minio' package is required. Install it with: "
                "pip install minio"
            ) from exc

        endpoint = self._env_or_input(self.endpoint, "MINIO_ENDPOINT")
        
        # Bersihkan string endpoint dari "https://" atau "http://" 
        if endpoint:
            endpoint = endpoint.replace("https://", "").replace("http://", "").rstrip("/")

        access_key = self._env_or_input(self.access_key, "MINIO_ACCESS_KEY")
        secret_key = self._env_or_input(self.secret_key, "MINIO_SECRET_KEY")

        if not endpoint:
            raise ValueError("MinIO endpoint is missing.")
        if not access_key:
            raise ValueError("MinIO access key is missing.")
        if not secret_key:
            raise ValueError("MinIO secret key is missing.")

        return Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=bool(self.secure),
        )

    # ------------------------------------------------------------------
    # Input normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _unwrap_input(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, Data):
            return value.data
            
        data_attr = getattr(value, "data", None)
        if data_attr is not None:
            return data_attr
            
        text_attr = getattr(value, "text", None)
        if text_attr is not None:
            return text_attr

        return value

    # ------------------------------------------------------------------
    # Base64 parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_base64(value: Any) -> tuple[bytes, str | None]:
        value = MinIOUpload._unwrap_input(value)

        if value is None:
            raise ValueError("Base64 input is empty.")

        detected_content_type: str | None = None

        if isinstance(value, dict):
            # Ditambahkan 'audio_base64' untuk menangkap output ElevenLabs
            base64_value = (
                value.get("base64")
                or value.get("file_base64")
                or value.get("audio_base64")
                or value.get("data")
                or value.get("content")
                or value.get("value")
                or value.get("text")
            )

            if isinstance(base64_value, dict):
                return MinIOUpload._decode_base64(base64_value)

            if base64_value is None:
                raise ValueError(
                    "Base64 data was not found in the input object. "
                    "Expected keys like base64, file_base64, audio_base64, etc."
                )

            detected_content_type = (
                value.get("content_type")
                or value.get("mime_type")
                or value.get("mime")
            )
            raw = base64_value

        elif isinstance(value, str):
            raw = value.strip()
        else:
            raise TypeError(f"Unsupported Base64 input type: {type(value).__name__}")

        if not raw:
            raise ValueError("Base64 input is empty.")

        data_uri_match = re.match(
            r"^data:([^;,]+)?(?:;[^,]*)?;base64,(.*)$",
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if data_uri_match:
            uri_content_type = data_uri_match.group(1)
            raw = data_uri_match.group(2)
            if uri_content_type:
                detected_content_type = uri_content_type

        raw = re.sub(r"\s+", "", raw)

        try:
            decoded = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid Base64 data.") from exc

        if not decoded:
            raise ValueError("Decoded Base64 file is empty.")

        return decoded, detected_content_type

    # ------------------------------------------------------------------
    # Metadata & Key Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _metadata_dict(metadata: Any) -> dict[str, str]:
        if metadata is None:
            return {}
        metadata = MinIOUpload._unwrap_input(metadata)
        if not metadata:
            return {}
        if not isinstance(metadata, dict):
            raise ValueError("Metadata must contain a dictionary/object.")
        return {
            str(key): str(value)
            for key, value in metadata.items()
            if value is not None
        }

    @staticmethod
    def _safe_join(prefix: str, filename: str) -> str:
        filename = Path(filename).name
        if not filename:
            raise ValueError("Filename cannot be empty.")
        prefix = (prefix or "").replace("\\", "/").strip("/")
        if not prefix:
            return filename
        parts = [part for part in prefix.split("/") if part and part not in (".", "..")]
        return "/".join(parts + [filename])

    def _check_existing(self, client, bucket: str, object_key: str) -> bool:
        try:
            client.stat_object(bucket, object_key)
            return True
        except Exception as exc:
            error_code = getattr(exc, "code", None)
            status_code = getattr(exc, "status", None)
            if error_code in {"NoSuchKey", "NoSuchObject", "NotFound"} or status_code == 404:
                return False
            raise

    # ------------------------------------------------------------------
    # Main Output Execution
    # ------------------------------------------------------------------

    def _upload_one(self, client, bucket: str, file_bytes: bytes, filename: str, object_key: str, content_type: str, user_metadata: dict[str, str]) -> dict[str, Any]:
        file_size = len(file_bytes)
        metadata = {
            "x-amz-meta-original-filename": filename,
            **{f"x-amz-meta-{key}": value for key, value in user_metadata.items()},
        }

        exists = self._check_existing(client, bucket, object_key)
        if exists and not self.overwrite:
            raise FileExistsError(f"Object already exists: {bucket}/{object_key}")

        stream = BytesIO(file_bytes)
        result = client.put_object(
            bucket_name=bucket,
            object_name=object_key,
            data=stream,
            length=file_size,
            content_type=content_type,
            metadata=metadata or None,
        )

        item: dict[str, Any] = {
            "success": True,
            "filename": filename,
            "object_key": object_key,
            "bucket": bucket,
            "size": file_size,
            "content_type": content_type,
            "etag": getattr(result, "etag", None),
            "version_id": getattr(result, "version_id", None),
        }

        if self.generate_presigned_url:
            expiry = max(1, int(self.presigned_url_expiry or 3600))
            item["presigned_url"] = client.presigned_get_object(
                bucket,
                object_key,
                expires=timedelta(seconds=expiry),
            )
            item["presigned_url_expires_in"] = expiry

        return item

    def upload_file(self) -> Data:
        if self.file_input is None:
            raise ValueError("Base64 File input is required.")

        # Ekstrak string dengan aman menggunakan helper baru (_extract_string)
        bucket_val = self._extract_string(self.bucket)
        bucket = self._env_or_input(bucket_val, "MINIO_BUCKET")

        if not bucket:
            raise ValueError("MinIO bucket is missing.")

        filename = self._extract_string(self.filename).strip()
        if not filename:
            raise ValueError("Filename is required when using Base64 input.")

        object_prefix = self._extract_string(self.object_prefix).strip("/")
        object_name = self._extract_string(self.object_name).strip()
        metadata = self._metadata_dict(self.metadata)

        # Decode Base64
        file_bytes, detected_content_type = self._decode_base64(self.file_input)

        content_type = self._extract_string(self.content_type).strip()
        if not content_type:
            content_type = (
                detected_content_type
                or mimetypes.guess_type(filename)[0]
                or "application/octet-stream"
            )

        final_filename = Path(object_name).name if object_name else Path(filename).name
        object_key = self._safe_join(object_prefix, final_filename)

        client = self._get_client()

        if not client.bucket_exists(bucket):
            raise ValueError(f"MinIO bucket does not exist: {bucket}")

        result = self._upload_one(
            client=client,
            bucket=bucket,
            file_bytes=file_bytes,
            filename=filename,
            object_key=object_key,
            content_type=content_type,
            user_metadata=metadata,
        )

        # MENGKONSTRUKSI URL LENGKAP (DIRECT URL)
        raw_endpoint = self._env_or_input(self.endpoint, "MINIO_ENDPOINT")
        clean_endpoint = raw_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        scheme = "https" if self.secure else "http"
        file_url = f"{scheme}://{clean_endpoint}/{bucket}/{object_key}"

        self.status = (
            f"Uploaded '{filename}' to "
            f"MinIO bucket '{bucket}' "
            f"as '{object_key}'."
        )

        return Data(
            data={
                "success": True,
                "filename": filename,
                "object_key": object_key,
                "bucket": bucket,
                "size": len(file_bytes),
                "content_type": content_type,
                "url": file_url,                 # <--- Field url ditambahkan di sini
                "etag": result.get("etag"),
                "version_id": result.get("version_id"),
                **(
                    {
                        "presigned_url": result["presigned_url"],
                        "presigned_url_expires_in": result["presigned_url_expires_in"],
                    }
                    if "presigned_url" in result
                    else {}
                ),
            }
        )
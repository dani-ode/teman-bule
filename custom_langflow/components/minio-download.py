from __future__ import annotations

import base64
import os
from typing import Any

from lfx.custom import Component
from lfx.io import (
    BoolInput,
    Output,
    StrInput,
    SecretStrInput,
)
from lfx.schema import Data


class MinIODownloadBase64(Component):
    """
    Download files from an S3-compatible MinIO server and convert them directly to Base64.
    """

    display_name = "MinIO Download to Base64"
    description = (
        "Download a file from MinIO/S3-compatible storage based on its Object Key "
        "and automatically encode it into a Base64 string."
    )
    documentation = "https://min.io/docs/minio/linux/developers/python/API.html"
    icon = "Download"
    name = "minio_download_base64"

    inputs = [
        StrInput(
            name="bucket",
            display_name="Bucket",
            info="MinIO bucket. Defaults to MINIO_BUCKET.",
            input_types=["Text", "Message"],
            required=True,
            value="",
        ),
        StrInput(
            name="object_key",
            display_name="Object Key",
            info="The full path and name of the object to download (e.g., folder/file.pdf).",
            input_types=["Text", "Message"],
            required=True,
            value="",
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
            display_name="Download Result",
            method="download_to_base64",
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
        text_attr = getattr(value, "text", None)
        if isinstance(text_attr, str):
            return text_attr
        if isinstance(value, dict) and "text" in value:
            return str(value["text"])
        return str(value)

    # ------------------------------------------------------------------
    # MinIO Client
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
    # Main Execution
    # ------------------------------------------------------------------

    def download_to_base64(self) -> Data:
        # Ekstrak nama bucket dan validasi
        bucket_val = self._extract_string(self.bucket)
        bucket = self._env_or_input(bucket_val, "MINIO_BUCKET")
        if not bucket:
            raise ValueError("MinIO bucket is required.")

        # Ekstrak object key
        object_key = self._extract_string(self.object_key).strip()
        if not object_key:
            raise ValueError("Object Key is required.")

        client = self._get_client()

        try:
            # Unduh file dari MinIO
            response = client.get_object(bucket, object_key)
            file_bytes = response.read()
            
            # Ambil content-type asli jika tersedia
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            
            # Sangat penting untuk menutup koneksi response
            response.close()
            response.release_conn()
            
        except Exception as exc:
            raise RuntimeError(f"Failed to download object '{object_key}' from bucket '{bucket}': {str(exc)}") from exc

        # Konversi byte array ke Base64
        base64_encoded = base64.b64encode(file_bytes).decode("utf-8")
        
        # Susun Data URI untuk kompatibilitas LLM/Langflow
        data_uri = f"data:{content_type};base64,{base64_encoded}"

        self.status = f"Downloaded '{object_key}' from '{bucket}' and converted to Base64."

        # Mengembalikan data base64 dengan metadatanya
        return Data(
            data={
                "success": True,
                "bucket": bucket,
                "object_key": object_key,
                "content_type": content_type,
                "size": len(file_bytes),
                "base64": base64_encoded,       # Raw Base64 string
                "data_uri": data_uri,           # Formatted Data URI string (mis. data:image/jpeg;base64,/9j/4AA...)
            }
        )
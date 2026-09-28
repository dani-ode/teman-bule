"""Adapter konkret S3-compatible media storage (DEC-15): presigned URLs.

Menyediakan dua factory callable sesuai kontrak router media
(``temanbule.api.routers.media``):

- ``media_upload_url_factory(storage_key) -> str``: presigned URL ``put_object``
  agar klien mengunggah langsung ke bucket tanpa melewati backend.
- ``media_download_url_factory(storage_key) -> str``: presigned URL
  ``get_object`` untuk unduhan scoped per media.

Kedua callable sinkron (``Callable[[str], str]``) persis seperti dipakai
router; pembuatan presigned URL oleh botocore adalah operasi lokal (signing),
tanpa request jaringan, sehingga aman dipanggil dari handler async.

Penandatanganan memakai boto3/botocore (dependency ``boto3`` di
requirements.txt). Access key/secret tidak pernah masuk log, error, maupun URL
di luar query signature standar SigV4. Endpoint non-HTTPS ditolak di luar
mode development. Kegagalan konfigurasi/signing → ``DependencyUnavailableError``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import boto3  # type: ignore[import-untyped]  # boto3-stubs belum dipasang
from botocore.client import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

_PUT_OBJECT = "put_object"
_GET_OBJECT = "get_object"


@dataclass(frozen=True)
class S3AdapterConfig:
    """Konfigurasi tepercaya untuk adapter; dibangun hanya dari settings backend."""

    endpoint_url: str
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    force_path_style: bool
    signed_url_ttl_seconds: int
    max_upload_bytes: int


class S3PresignedUrlFactory:
    """Penghasil presigned PUT/GET URL untuk storage key media."""

    def __init__(self, config: S3AdapterConfig) -> None:
        self._config = config
        addressing: dict[str, str] = {"addressing_style": "path"} if config.force_path_style else {}
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            region_name=config.region,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            config=Config(signature_version="s3v4", s3=addressing),
        )

    def build_upload_url(self, storage_key: str) -> str:
        """Presigned PUT URL untuk satu storage key (scoped, TTL terbatas)."""
        return self._presign(_PUT_OBJECT, storage_key)

    def build_download_url(self, storage_key: str) -> str:
        """Presigned GET URL untuk satu storage key (scoped, TTL terbatas)."""
        return self._presign(_GET_OBJECT, storage_key)

    def _presign(self, operation: str, storage_key: str) -> str:
        """Tandatangani URL; kegagalan → DependencyUnavailableError tanpa secret."""
        if not storage_key.strip():
            raise DependencyUnavailableError(
                "Storage key media kosong.", code="S3_STORAGE_KEY_INVALID"
            )
        try:
            return str(
                self._client.generate_presigned_url(
                    operation,
                    Params={"Bucket": self._config.bucket, "Key": storage_key},
                    ExpiresIn=self._config.signed_url_ttl_seconds,
                )
            )
        except (BotoCoreError, ClientError) as exc:
            raise DependencyUnavailableError(
                "Gagal membuat signed URL media.",
                code="S3_PRESIGN_FAILED",
            ) from exc


def _validate_config(config: S3AdapterConfig, *, app_env: str) -> None:
    """Validasi keamanan minimal tanpa menyebut nilai konfigurasi."""
    if not config.endpoint_url.startswith("https://") and app_env.lower() in {
        "production",
        "prod",
    }:
        raise ValueError("S3 endpoint wajib HTTPS di production")
    if config.signed_url_ttl_seconds <= 0:
        raise ValueError("S3 signed URL TTL harus positif")
    if config.max_upload_bytes <= 0:
        raise ValueError("S3 max upload bytes harus positif")


def _build_factory(settings: Settings) -> S3PresignedUrlFactory:
    """Bangun factory terkonfigurasi; ValueError tanpa nilai bila config kurang."""
    required = {
        "s3_endpoint_url": settings.s3_endpoint_url,
        "s3_region": settings.s3_region,
        "s3_bucket": settings.s3_bucket,
        "s3_access_key_id": settings.s3_access_key_id,
        "s3_secret_access_key": settings.s3_secret_access_key,
    }
    missing = [
        name for name, value in required.items() if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError("Konfigurasi S3 tidak lengkap: " + ", ".join(sorted(missing)))
    config = S3AdapterConfig(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
        force_path_style=settings.s3_force_path_style,
        signed_url_ttl_seconds=settings.s3_signed_url_ttl_seconds,
        max_upload_bytes=settings.s3_max_upload_bytes,
    )
    _validate_config(config, app_env=settings.app_env)
    return S3PresignedUrlFactory(config)


def build_upload_url_factory(settings: Settings) -> Callable[[str], str]:
    """Callable ``(storage_key) -> presigned PUT URL`` untuk app.state."""
    factory = _build_factory(settings)
    return factory.build_upload_url


def build_download_url_factory(settings: Settings) -> Callable[[str], str]:
    """Callable ``(storage_key) -> presigned GET URL`` untuk app.state."""
    factory = _build_factory(settings)
    return factory.build_download_url

"""Field-level encryption adapter untuk secrets at-rest (BYOK credentials).

KEK berasal dari settings (`crypto_key_encryption_key`), 32-byte urlsafe
base64 (Fernet). Konfigurasi kosong → ConfigurationError (fail-fast, tanpa
fallback). Plaintext tidak pernah dilog; fingerprint SHA-256 untuk dedupe/
identifikasi tanpa membuka ciphertext.
"""

from __future__ import annotations

import binascii

from cryptography.fernet import Fernet, InvalidToken

from temanbule.platform.errors import AppError
from temanbule.platform.settings import ConfigurationError


class DecryptionError(AppError):
    status_code = 500
    code = "DECRYPTION_FAILED"


class FieldCipher:
    """Enkripsi/dekripsi field secrets dengan KEK terkonfigurasi."""

    def __init__(self, key_encryption_key: str) -> None:
        if not key_encryption_key.strip():
            raise ConfigurationError(["CRYPTO_KEY_ENCRYPTION_KEY"])
        try:
            self._fernet = Fernet(key_encryption_key.encode())
        except (ValueError, binascii.Error) as exc:
            raise ConfigurationError(["CRYPTO_KEY_ENCRYPTION_KEY (format base64 32-byte)"]) from exc

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise ValueError("plaintext kosong tidak dienkripsi")
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError("Ciphertext tidak dapat didekripsi.") from exc

    @staticmethod
    def generate_kek() -> str:
        """Helper operasional: hasilkan KEK baru (untuk .env operator)."""
        return Fernet.generate_key().decode()

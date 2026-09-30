"""Handler event auth.email_requested.v1 (FND-06).

Dipanggil dari outbox dispatcher; delivery at-least-once sehingga handler harus
idempoten. Dedupe via tabel auth_email_deliveries; token verifikasi/reset yang
sudah dipakai (consumed) atau tertimpa token baru tidak akan dikirim ulang.

Link mengarah sebagai DEEP LINK ke aplikasi Expo (scheme `temanbule://`) karena
frontend adalah app Android/iOS, bukan web. App membuka route
Auth/VerifyEmail atau Auth/ResetPassword, lalu memanggil
POST /v1/auth/email:verify atau /v1/auth/password:reset dengan token dari
query string.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.platform.mailer import SmtpMailer
from temanbule.platform.settings import Settings

logger = logging.getLogger(__name__)

_SUBJECTS = {
    "email_verify": "Verifikasi email Teman Bule Anda",
    "password_reset": "Reset password Teman Bule Anda",
}

# Deep-link path sesuai linking.ts di app Expo (src/core/navigation/linking.ts):
#   VerifyEmail:   'auth/verify-email'
#   ResetPassword: 'auth/reset-password'
_PATHS = {
    "email_verify": "auth/verify-email",
    "password_reset": "auth/reset-password",
}

_BODIES = {
    "email_verify": (
        "Halo,\n\n"
        "Ketuk link berikut untuk memverifikasi email Anda "
        "(akan terbuka di aplikasi Teman Bule):\n{link}\n\n"
        "Link berlaku 30 menit. Abaikan email ini bila Anda tidak merasa mendaftar."
    ),
    "password_reset": (
        "Halo,\n\n"
        "Ketuk link berikut untuk mengatur ulang password Anda "
        "(akan terbuka di aplikasi Teman Bule):\n{link}\n\n"
        "Link berlaku 30 menit. Abaikan email ini bila Anda tidak meminta reset password."
    ),
}


def _app_link(settings: Settings, path: str, token: str) -> str:
    """Bangun deep link app dari scheme yang dikonfigurasi (default temanbule://)."""
    scheme = (settings.auth_app_deep_link_scheme or "temanbule").strip().rstrip(":/")
    return f"{scheme}://{path}?token={quote(token, safe='')}"


async def handle_auth_email_requested(
    session: AsyncSession, payload: dict[str, Any], settings: Settings
) -> None:
    """Kirim email verifikasi/reset via SMTP. Gagal SMTP → raise → dead-letter."""
    purpose = str(payload.get("purpose", ""))
    email = str(payload.get("email", ""))
    token = str(payload.get("token", ""))
    if purpose not in _SUBJECTS or not email or not token:
        logger.warning(
            "auth_email_payload_invalid",
            extra={"purpose": purpose, "has_email": bool(email)},
        )
        return  # payload rusak permanen; jangan retry

    # Idempoten: token single-use disimpan sha256 di DB. Bila sudah consumed
    # ATAU sudah digantikan token baru untuk purpose yang sama (resend), skip —
    # berarti event ini replay dari delivery sebelumnya.
    from sqlalchemy import func, select

    from temanbule.modules.identity.models import AuthActionToken

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    token_row = (
        await session.execute(
            select(AuthActionToken).where(AuthActionToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if token_row is None or token_row.consumed_at is not None:
        logger.info("auth_email_skipped_token_consumed", extra={"purpose": purpose})
        return

    # Hanya kirim untuk token TERBARU milik user+purpose ini; token lama yang
    # belum consumed sudah tidak berguna bagi user (link terbaru yang di-email).
    newer_exists = (
        await session.execute(
            select(func.count())
            .select_from(AuthActionToken)
            .where(
                AuthActionToken.user_id == token_row.user_id,
                AuthActionToken.purpose == purpose,
                AuthActionToken.created_at > token_row.created_at,
            )
        )
    ).scalar_one()
    if newer_exists:
        logger.info("auth_email_skipped_token_superseded", extra={"purpose": purpose})
        return

    # Dedupe atomik antar delivery konkuren dari event yang sama (relay race):
    # baris dedupe di-insert SEBELUM kirim; INSERT yang kalah (duplicate key)
    # melempar IntegrityError → delivery kedua di-skip. Token itu sendiri TIDAK
    # dikonsumsi di sini supaya link di email tetap bisa diklik user.
    from sqlalchemy import text

    try:
        await session.execute(
            text(
                "INSERT INTO auth_email_deliveries (token_hash, purpose) "
                "VALUES (:th, :p)"
            ),
            {"th": token_hash, "p": purpose},
        )
        await session.flush()
    except Exception:
        logger.info("auth_email_skipped_duplicate_delivery", extra={"purpose": purpose})
        return

    link = _app_link(settings, _PATHS[purpose], token)
    body = _BODIES[purpose].format(link=link)

    mailer = SmtpMailer(settings)
    await mailer.send(to_address=email, subject=_SUBJECTS[purpose], body=body)
    logger.info("auth_email_sent", extra={"purpose": purpose})

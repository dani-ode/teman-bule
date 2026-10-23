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
# Slug purpose pada endpoint /v1/auth/redirect/{purpose} (auth_redirect.py),
# yang selanjutnya me-redirect ke deep-link path app (auth/verify-email atau
# auth/reset-password sesuai src/core/navigation/linking.ts).
_REDIRECT_SLUGS = {
    "email_verify": "verify-email",
    "password_reset": "reset-password",
}

_BODIES = {
    "email_verify": (
        "Halo,\n\n"
        "Klik tombol berikut untuk memverifikasi email Anda "
        "(akan terbuka di aplikasi Teman Bule):\n{link}\n\n"
        "Link berlaku 30 menit. Abaikan email ini bila Anda tidak merasa mendaftar."
    ),
    "password_reset": (
        "Halo,\n\n"
        "Klik tombol berikut untuk mengatur ulang password Anda "
        "(akan terbuka di aplikasi Teman Bule):\n{link}\n\n"
        "Link berlaku 30 menit. Abaikan email ini bila Anda tidak meminta reset password."
    ),
}

_BUTTON_LABELS = {
    "email_verify": "Verifikasi Email",
    "password_reset": "Reset Password",
}

# Template HTML email (inline CSS untuk kompatibilitas email client).
# String di-concat per-baris agar mematuhi batas panjang baris lint.
_HTML_TEMPLATE = (
    '<!DOCTYPE html>\n<html lang="id">\n<head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
    "</head>\n"
    '<body style="margin:0;padding:0;background-color:#f4f4f5;'
    'font-family:Arial,Helvetica,sans-serif;">\n'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="background-color:#f4f4f5;padding:24px 0;">\n'
    '<tr><td align="center">\n'
    '<table role="presentation" width="480" cellpadding="0" cellspacing="0" '
    'style="background-color:#ffffff;border-radius:8px;padding:32px;">\n'
    "<tr><td>\n"
    '<h1 style="margin:0 0 16px;font-size:20px;color:#18181b;">{heading}</h1>\n'
    '<p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#3f3f46;">'
    "{intro}</p>\n"
    '<table role="presentation" cellpadding="0" cellspacing="0" '
    'style="margin:0 0 24px;">\n'
    '<tr><td align="center" bgcolor="#2563eb" style="border-radius:6px;">\n'
    '<a href="{link}" target="_blank" style="display:inline-block;'
    "padding:12px 32px;font-size:16px;font-weight:bold;color:#ffffff;"
    'text-decoration:none;border-radius:6px;">{button}</a>\n'
    "</td></tr>\n</table>\n"
    '<p style="margin:0 0 8px;font-size:12px;line-height:1.6;color:#71717a;">'
    "{footnote}</p>\n"
    '<p style="margin:0;font-size:12px;line-height:1.6;color:#a1a1aa;'
    'word-break:break-all;">Bila tombol tidak berfungsi, salin link ini ke '
    "browser:<br>{link}</p>\n"
    "</td></tr>\n</table>\n</td></tr>\n</table>\n</body>\n</html>\n"
)

_HTML_CONTENT = {
    "email_verify": {
        "heading": "Verifikasi email Anda",
        "intro": (
            "Klik tombol di bawah untuk memverifikasi email Anda "
            "(akan terbuka di aplikasi Teman Bule)."
        ),
        "footnote": (
            "Link berlaku 30 menit. Abaikan email ini bila Anda tidak merasa mendaftar."
        ),
    },
    "password_reset": {
        "heading": "Reset password Anda",
        "intro": (
            "Klik tombol di bawah untuk mengatur ulang password Anda "
            "(akan terbuka di aplikasi Teman Bule)."
        ),
        "footnote": (
            "Link berlaku 30 menit. "
            "Abaikan email ini bila Anda tidak meminta reset password."
        ),
    },
}


def _html_body(purpose: str, link: str) -> str:
    content = _HTML_CONTENT[purpose]
    return _HTML_TEMPLATE.format(
        heading=content["heading"],
        intro=content["intro"],
        footnote=content["footnote"],
        button=_BUTTON_LABELS[purpose],
        link=link,
    )


def _app_link(settings: Settings, purpose: str, token: str) -> str:
    """Bangun link email HTTPS yang bisa diklik.

    Email client hanya mengaktifkan hyperlink http(s)://, sehingga kita kirim
    link HTTPS ke endpoint backend /v1/auth/redirect/{slug}?token=... yang
    me-redirect ke deep link app. Deep link `temanbule://` mentah tidak bisa
    diklik di Gmail/Outlook.

    Base diambil dari auth_email_link_base_url, fallback ke app_public_url.
    """
    base = (settings.auth_email_link_base_url or settings.app_public_url).rstrip("/")
    slug = _REDIRECT_SLUGS[purpose]
    return f"{base}/v1/auth/redirect/{slug}?token={quote(token, safe='')}"


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

    link = _app_link(settings, purpose, token)
    body = _BODIES[purpose].format(link=link)
    html_body = _html_body(purpose, link)

    mailer = SmtpMailer(settings)
    await mailer.send(
        to_address=email, subject=_SUBJECTS[purpose], body=body, html_body=html_body
    )
    logger.info("auth_email_sent", extra={"purpose": purpose})

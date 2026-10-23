"""Redirect link email auth (verifikasi/reset) ke deep link aplikasi.

Email client (Gmail, Outlook, webmail) hanya mengaktifkan hyperlink http(s)://,
sehingga deep link `temanbule://...` mentah di email tidak bisa diklik. Modul ini
mengekspos endpoint HTTPS publik yang menerima token lalu me-redirect (HTTP 302)
ke deep link app. Bila app tidak terpasang / dibuka di browser, halaman fallback
HTML menampilkan instruksi + tombol yang mencoba membuka app via JavaScript.

Keamanan: token tetap single-use + hash-only di DB; endpoint ini hanya meneruskan
token via URL fragment-safe redirect (token sudah ada di email user, bukan data
baru). Token tidak pernah di-log atau disimpan di sini.
"""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from temanbule.api.deps import SettingsDep

router = APIRouter(prefix="/v1/auth", tags=["auth-redirect"])

# Map purpose → deep-link path di app Expo (src/core/navigation/linking.ts).
_DEEP_LINK_PATHS = {
    "verify-email": "auth/verify-email",
    "reset-password": "auth/reset-password",
}

_PAGE_TITLES = {
    "verify-email": "Verifikasi Email",
    "reset-password": "Reset Password",
}

_FALLBACK_PAGE = (
    '<!DOCTYPE html>\n<html lang="id">\n<head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
    "<title>{title}</title></head>\n"
    '<body style="margin:0;padding:0;background-color:#f4f4f5;'
    'font-family:Arial,Helvetica,sans-serif;">\n'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="background-color:#f4f4f5;padding:48px 16px;">\n'
    '<tr><td align="center">\n'
    '<table role="presentation" width="480" cellpadding="0" cellspacing="0" '
    'style="background-color:#ffffff;border-radius:8px;padding:32px;">\n'
    "<tr><td>\n"
    '<h1 style="margin:0 0 16px;font-size:20px;color:#18181b;">{title}</h1>\n'
    '<p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#3f3f46;">'
    "Membuka aplikasi Teman Bule&hellip; Bila tidak terbuka otomatis, "
    "ketuk tombol di bawah.</p>\n"
    '<table role="presentation" cellpadding="0" cellspacing="0" '
    'style="margin:0 0 24px;">\n'
    '<tr><td align="center" bgcolor="#2563eb" style="border-radius:6px;">\n'
    '<a href="{deep_link}" style="display:inline-block;padding:12px 32px;'
    'font-size:16px;font-weight:bold;color:#ffffff;text-decoration:none;'
    'border-radius:6px;">Buka Aplikasi</a>\n'
    "</td></tr>\n</table>\n"
    '<p style="margin:0;font-size:12px;line-height:1.6;color:#71717a;">'
    "Link ini hanya berfungsi pada perangkat dengan aplikasi Teman Bule "
    "terpasang.</p>\n"
    "</td></tr>\n</table>\n</td></tr>\n</table>\n"
    "<script>window.location.href = {deep_link_js};</script>\n"
    "</body>\n</html>\n"
)


@router.get("/redirect/{purpose}", response_class=HTMLResponse, response_model=None)
async def auth_email_redirect(
    purpose: str, settings: SettingsDep, token: str = ""
) -> HTMLResponse | RedirectResponse:
    """Terima link email HTTPS → redirect ke deep link app.

    Memakai halaman HTML kecil dengan auto-redirect JS agar bekerja baik di
    email client maupun browser; deep link scheme tidak didukung anchor di
    banyak email client, jadi kita andalkan halaman perantara.
    """
    path = _DEEP_LINK_PATHS.get(purpose)
    if path is None or not token:
        # purpose tak dikenal / token kosong → arahkan ke error frontend
        return RedirectResponse(url=f"{settings.auth_frontend_error_url}?reason=invalid_link")

    scheme = (settings.auth_app_deep_link_scheme or "temanbule").strip().rstrip(":/")
    deep_link = f"{scheme}://{path}?token={quote(token, safe='')}"
    title = _PAGE_TITLES.get(purpose, "Teman Bule")

    # Selalu render halaman fallback dengan auto-redirect JS. Anchor langsung ke
    # custom scheme sering diblokir email client; halaman perantara lebih andal.
    html = _FALLBACK_PAGE.format(
        title=escape(title),
        deep_link=escape(deep_link, quote=True),
        deep_link_js=_js_string(deep_link),
    )
    return HTMLResponse(content=html)


def _js_string(value: str) -> str:
    """Encode string sebagai JS string literal yang aman (JSON-compatible)."""
    import json

    return json.dumps(value)

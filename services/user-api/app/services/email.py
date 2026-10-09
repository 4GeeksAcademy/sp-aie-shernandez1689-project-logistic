import json
import os
from html import escape
from urllib.error import URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.security import PASSWORD_RESET_EXPIRE_MINUTES


class EmailDeliveryError(Exception):
    pass


def send_password_reset_email(recipient: str, token: str) -> None:
    api_key = os.getenv("RESEND_API_KEY")
    sender = os.getenv("EMAIL_FROM")
    reset_url = os.getenv("RESET_PASSWORD_URL")
    if not api_key or not sender or not reset_url:
        raise EmailDeliveryError("Missing email configuration")

    try:
        parts = urlsplit(reset_url)
        if not parts.hostname or not (
            parts.scheme == "https"
            or (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"})
        ) or parts.username or parts.password:
            raise ValueError("Invalid reset URL")
        query = [(name, value) for name, value in parse_qsl(parts.query) if name != "token"]
        query.append(("token", token))
        link = urlunsplit(parts._replace(query=urlencode(query)))
    except ValueError as exc:
        raise EmailDeliveryError("Invalid reset URL configuration") from exc

    safe_link = escape(link, quote=True)
    message = {
        "from": sender,
        "to": [recipient],
        "subject": "Restablece tu contrasena de TrackFlow",
        "text": (
            "Recibimos una solicitud para restablecer tu contrasena de TrackFlow.\n\n"
            f"Abre este enlace: {link}\n\n"
            f"El enlace caduca en {PASSWORD_RESET_EXPIRE_MINUTES} minutos y solo puede usarse una vez.\n"
            "Si no solicitaste este cambio, ignora este correo."
        ),
        "html": (
            '<!doctype html><html lang="es"><head><meta name="viewport" '
            'content="width=device-width, initial-scale=1"></head>'
            '<body style="margin:0;padding:24px 16px;font-family:Arial,sans-serif;'
            'font-size:16px;line-height:1.6;color:#222;background:#f5f5f5">'
            '<div style="max-width:560px;margin:0 auto">'
            '<h1 style="font-size:24px;line-height:1.3">Restablece tu contrasena</h1>'
            '<p>Recibimos una solicitud para restablecer tu contrasena de TrackFlow.</p>'
            f'<p><a href="{safe_link}" style="display:inline-block;padding:12px 20px;'
            'background:#166534;color:#fff;text-decoration:none;border-radius:4px">'
            'Restablecer contrasena</a></p>'
            f'<p>El enlace caduca en {PASSWORD_RESET_EXPIRE_MINUTES} minutos '
            'y solo puede usarse una vez.</p>'
            '<p>Si el boton no funciona, abre este enlace:</p>'
            f'<p style="overflow-wrap:anywhere;word-break:break-all"><a href="{safe_link}">'
            f'{safe_link}</a></p>'
            '<p>Si no solicitaste este cambio, ignora este correo.</p></div></body></html>'
        ),
    }
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(message).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "TrackFlow-User-API/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise EmailDeliveryError("Email provider rejected the message")
    except (URLError, OSError) as exc:
        raise EmailDeliveryError("Email delivery failed") from exc
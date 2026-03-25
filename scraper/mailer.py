"""
SMTP / Brevo API email sender.
If brevo_api_key is provided, sends via Brevo REST API (enables open/click tracking).
Falls back to SMTP otherwise.
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import httpx

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp-relay.brevo.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_LOGIN = os.environ.get("SMTP_LOGIN", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "info@soft-synergy.com")
FROM_NAME = os.environ.get("FROM_NAME", "Antoni Seba")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")


def get_user_smtp_config(user) -> dict:
    """Extract per-user SMTP/Brevo config from a User model instance."""
    return {
        'smtp_host': getattr(user, 'smtp_host', None),
        'smtp_port': getattr(user, 'smtp_port', None),
        'smtp_login': getattr(user, 'smtp_login', None),
        'smtp_password': getattr(user, 'smtp_password', None),
        'from_email': getattr(user, 'from_email', None),
        'from_name': getattr(user, 'from_name', None),
        'brevo_api_key': getattr(user, 'brevo_api_key', None),
    }


def send_email(to: str, subject: str, body: str, smtp_config: dict = None) -> Optional[str]:
    """
    Send email. Returns Brevo messageId if sent via API (enables tracking), else None.
    Raises on failure.
    """
    cfg = smtp_config or {}
    api_key = cfg.get('brevo_api_key') or BREVO_API_KEY
    from_email_addr = cfg.get('from_email') or FROM_EMAIL
    from_name_val = cfg.get('from_name') or FROM_NAME

    html_body = "<br>\n".join(line for line in body.splitlines())

    if api_key:
        r = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={
                "sender": {"name": from_name_val, "email": from_email_addr},
                "to": [{"email": to}],
                "subject": subject,
                "textContent": body,
                "htmlContent": f"<html><body style='font-family:sans-serif;font-size:14px;'>{html_body}</body></html>",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("messageId")

    # Fallback: SMTP
    host = cfg.get('smtp_host') or SMTP_HOST
    port = cfg.get('smtp_port') or SMTP_PORT
    login = cfg.get('smtp_login') or SMTP_LOGIN
    password = cfg.get('smtp_password') or SMTP_PASSWORD

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name_val} <{from_email_addr}>"
    msg["To"] = to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(f"<html><body style='font-family:sans-serif;font-size:14px;'>{html_body}</body></html>", "html", "utf-8"))

    with smtplib.SMTP(host, port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(login, password)
        smtp.sendmail(from_email_addr, to, msg.as_string())

    return None

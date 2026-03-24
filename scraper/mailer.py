"""
SMTP email sender via Brevo (smtp-relay.brevo.com:587).
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp-relay.brevo.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_LOGIN = os.environ.get("SMTP_LOGIN", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "info@soft-synergy.com")
FROM_NAME = os.environ.get("FROM_NAME", "Antoni Seba")


def get_user_smtp_config(user) -> dict:
    """Extract per-user SMTP config from a User model instance."""
    return {
        'smtp_host': getattr(user, 'smtp_host', None),
        'smtp_port': getattr(user, 'smtp_port', None),
        'smtp_login': getattr(user, 'smtp_login', None),
        'smtp_password': getattr(user, 'smtp_password', None),
        'from_email': getattr(user, 'from_email', None),
        'from_name': getattr(user, 'from_name', None),
    }


def send_email(to: str, subject: str, body: str, smtp_config: dict = None) -> None:
    """Send a plain-text email via SMTP. Uses per-user config when provided, falls back to env vars."""
    cfg = smtp_config or {}
    host = cfg.get('smtp_host') or SMTP_HOST
    port = cfg.get('smtp_port') or SMTP_PORT
    login = cfg.get('smtp_login') or SMTP_LOGIN
    password = cfg.get('smtp_password') or SMTP_PASSWORD
    from_email_addr = cfg.get('from_email') or FROM_EMAIL
    from_name_val = cfg.get('from_name') or FROM_NAME

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name_val} <{from_email_addr}>"
    msg["To"] = to

    # Plain text
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Simple HTML — preserve newlines
    html_body = "<br>\n".join(line for line in body.splitlines())
    msg.attach(MIMEText(f"<html><body style='font-family:sans-serif;font-size:14px;'>{html_body}</body></html>", "html", "utf-8"))

    with smtplib.SMTP(host, port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(login, password)
        smtp.sendmail(from_email_addr, to, msg.as_string())

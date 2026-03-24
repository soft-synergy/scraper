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


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email via Brevo SMTP. Raises on failure."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to

    # Plain text
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Simple HTML — preserve newlines
    html_body = "<br>\n".join(line for line in body.splitlines())
    msg.attach(MIMEText(f"<html><body style='font-family:sans-serif;font-size:14px;'>{html_body}</body></html>", "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_LOGIN, SMTP_PASSWORD)
        smtp.sendmail(FROM_EMAIL, to, msg.as_string())

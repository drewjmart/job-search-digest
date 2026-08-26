"""Send the digest via Gmail SMTP using an App Password from .env."""

import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

# Explicit path (rather than relying on cwd) so this works the same whether
# invoked manually, from cron, or from a launchd agent.
load_dotenv(Path(__file__).parent / ".env")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def send_digest_email(subject: str, body_markdown: str) -> None:
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    to_address = os.environ.get("DIGEST_TO_ADDRESS") or address

    if not address or not app_password:
        raise RuntimeError(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set. Copy .env.example to .env "
            "and fill them in (see README for how to create a Gmail App Password)."
        )

    msg = MIMEText(body_markdown, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = to_address

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(address, app_password)
        server.sendmail(address, [to_address], msg.as_string())

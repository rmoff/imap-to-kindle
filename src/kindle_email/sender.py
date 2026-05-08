"""Send a file (EPUB or PDF) to a Kindle email address via SMTP."""

from __future__ import annotations

import email.mime.application
import email.mime.multipart
import email.mime.text
import logging
import smtplib
import time

from .config import Config

log = logging.getLogger(__name__)


def send(filename: str, data: bytes, config: Config) -> bool:
    """
    Send a file as an email attachment to the configured Kindle address.
    Returns True on success, False on failure.

    Amazon's Send-to-Kindle service requires:
    - The sender must be in the Kindle's approved senders list
    - Attachment must be a supported format (EPUB and PDF are both supported)
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            _send_once(filename, data, config)
            log.info("Sent '%s' to %s", filename, config.kindle.address)
            return True
        except smtplib.SMTPAuthenticationError as e:
            log.error("SMTP authentication failed: %s", e)
            return False  # No point retrying auth failures
        except (smtplib.SMTPException, OSError) as e:
            if attempt == max_retries:
                log.error("Failed to send '%s' after %d attempts: %s", filename, max_retries, e)
                return False
            wait = 2 ** attempt
            log.warning("SMTP error (attempt %d/%d): %s — retrying in %ds", attempt, max_retries, e, wait)
            time.sleep(wait)
    return False


def _send_once(filename: str, data: bytes, config: Config) -> None:
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = config.kindle.from_address
    msg["To"] = config.kindle.address
    msg["Subject"] = "kindle-email delivery"

    # Amazon Send-to-Kindle ignores the body but needs something
    msg.attach(email.mime.text.MIMEText("Delivered by kindle-email.", "plain"))

    # Strip quotes and newlines from filename to prevent header injection
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")

    attachment = email.mime.application.MIMEApplication(
        data,
        Name=safe_name,
    )
    attachment["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    msg.attach(attachment)

    with smtplib.SMTP(config.smtp.host, config.smtp.port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(config.smtp.username, config.smtp.password)
        smtp.sendmail(config.kindle.from_address, config.kindle.address, msg.as_string())

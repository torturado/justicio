import logging as lg
import os

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Content, Email, Mail, To


def send_email(subject: str, content: str) -> None:
    """Email the ETL administrator (ETL_NOTIFICATION_EMAIL) through SendGrid, if both are configured."""
    logger = lg.getLogger(send_email.__name__)
    api_key = os.environ.get("SENDGRID_API_KEY")
    address = os.environ.get("ETL_NOTIFICATION_EMAIL")
    if not api_key or not address:
        logger.warning("Email not sent: SENDGRID_API_KEY and ETL_NOTIFICATION_EMAIL are required")
        return
    mail = Mail(Email(address), To(address), subject, Content("text/plain", content))
    response = SendGridAPIClient(api_key=api_key).client.mail.send.post(request_body=mail.get())
    logger.info("Sent email with status %s", response.status_code)

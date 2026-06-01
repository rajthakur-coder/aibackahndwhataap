import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import BackgroundTasks

from app.config import settings


def _send_email_sync(subject: str, recipients: list[str], body: str) -> None:
    if not settings.GMAIL_ID or not settings.GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_ID and GMAIL_APP_PASSWORD must be configured to send email")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = settings.GMAIL_ID
    message["To"] = ", ".join(recipients)
    message.attach(MIMEText(body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(settings.GMAIL_ID, settings.GMAIL_APP_PASSWORD)
        smtp.sendmail(settings.GMAIL_ID, recipients, message.as_string())


def send_email(subject: str, recipients: list[str], body: str, background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(_send_email_sync, subject, recipients, body)
    return {"message": "Email send triggered"}

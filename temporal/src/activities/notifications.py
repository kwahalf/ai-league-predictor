from __future__ import annotations
import smtplib
from email.message import EmailMessage
from temporalio import activity
from temporalio.exceptions import ApplicationError
from ..config import settings
from .client import operation_id, request
from .supabase_core import uuid


@activity.defn
def send_email(to: str, subject: str, body: str) -> bool:
    """Deliver to configured SMTP (local development defaults to the test inbox)."""
    if not to.strip() or '@' not in to or '\n' in to or '\r' in to:
        raise ApplicationError('A valid recipient email is required', non_retryable=True)
    message = EmailMessage()
    message['From'] = settings.smtp_from
    message['To'] = to
    message['Subject'] = subject
    message['Message-ID'] = f'<{operation_id("email")}@template.local>'
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        refused = smtp.send_message(message)
        if refused:
            raise ApplicationError('SMTP rejected recipient', non_retryable=True)
    return True


@activity.defn
def send_notification(user_id: str, message: str) -> bool:
    if not message.strip():
        raise ApplicationError('Notification message is required', non_retryable=True)
    request('notifications?on_conflict=id', {
        'id': operation_id('notification'), 'user_id': uuid(user_id), 'message': message,
    }, prefer='resolution=ignore-duplicates,return=minimal')
    return True

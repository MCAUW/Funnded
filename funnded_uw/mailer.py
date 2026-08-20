"""Sends the UW analysis back as a reply on the original email thread."""

import smtplib
from email.message import EmailMessage


def build_reply(cfg, submission, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = cfg.email_address
    msg["To"] = submission.reply_addr
    subject = submission.subject or "Deal submission"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    msg["Subject"] = subject
    if submission.message_id:
        msg["In-Reply-To"] = submission.message_id
        refs = f"{submission.references} {submission.message_id}".strip()
        msg["References"] = refs
    msg.set_content(body + "\n\n- UW\n")
    return msg


def send_reply(cfg, submission, body: str) -> None:
    msg = build_reply(cfg, submission, body)
    if cfg.smtp_port == 465:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port) as smtp:
            smtp.login(cfg.email_address, cfg.email_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as smtp:
            smtp.starttls()
            smtp.login(cfg.email_address, cfg.email_password)
            smtp.send_message(msg)

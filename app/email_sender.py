"""Envío de correos.

  - FileOutboxSender: NO envía nada; escribe cada correo en data/outbox (seguro para el demo).
  - SMTPSender      : envío real por SMTP. Sin probar en este proyecto contra un servidor real.
"""
from __future__ import annotations

import json
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Protocol

from .config import Settings
from .retry import PermanentError


class Sender(Protocol):
    name: str

    def send(self, *, to_email: str, to_name: str, subject: str, body: str, tag: str) -> str: ...


class FileOutboxSender:
    name = "file"

    def __init__(self, outbox_dir: str, sender_from: str):
        self.dir = Path(outbox_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sender_from = sender_from

    def send(self, *, to_email, to_name, subject, body, tag) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", f"{tag}__{to_email}")
        path = self.dir / f"{safe}.txt"
        path.write_text(
            f"From: {self.sender_from}\nTo: {to_name} <{to_email}>\nSubject: {subject}\n\n{body}\n",
            encoding="utf-8",
        )
        return str(path)


class SMTPSender:
    name = "smtp"

    def __init__(self, s: Settings):
        if not (s.smtp_host and s.smtp_user and s.smtp_password):
            raise PermanentError("Faltan SMTP_HOST / SMTP_USER / SMTP_PASSWORD")
        self.s = s

    def send(self, *, to_email, to_name, subject, body, tag) -> str:
        msg = EmailMessage()
        msg["From"] = self.s.email_from
        msg["To"] = f"{to_name} <{to_email}>"
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()
        # Cabecera estándar de baja: mejora la entregabilidad y cumple buenas prácticas.
        msg["List-Unsubscribe"] = f"<mailto:{self.s.email_from}?subject=BAJA>"
        msg.set_content(body)
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(self.s.smtp_user, self.s.smtp_password)
            smtp.send_message(msg)
        return msg["Message-ID"]


def build_sender(s: Settings) -> Sender:
    if s.email_sender == "smtp":
        return SMTPSender(s)
    return FileOutboxSender(s.outbox_dir, s.email_from)

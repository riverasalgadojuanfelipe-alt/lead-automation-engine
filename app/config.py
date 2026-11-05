"""Configuración leída de variables de entorno (ver .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _load_dotenv(path: str = ".env") -> None:
    # Cargador mínimo de .env para no depender de otra librería.
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.split("#", 1)[0].strip()
            os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_api_key: str
    llm_model: str
    webhook_secret: str
    email_sender: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    email_from: str
    db_path: str
    outbox_dir: str
    demo_ui: bool = True


def load_settings() -> Settings:
    _load_dotenv()
    g = os.environ.get
    return Settings(
        llm_provider=g("LLM_PROVIDER", "mock").lower(),
        llm_api_key=g("LLM_API_KEY", ""),
        llm_model=g("LLM_MODEL", ""),
        webhook_secret=g("WEBHOOK_SECRET", ""),
        email_sender=g("EMAIL_SENDER", "file").lower(),
        smtp_host=g("SMTP_HOST", ""),
        smtp_port=int(g("SMTP_PORT", "587") or 587),
        smtp_user=g("SMTP_USER", ""),
        smtp_password=g("SMTP_PASSWORD", ""),
        email_from=g("EMAIL_FROM", "ventas@example.com"),
        db_path=g("DB_PATH", "data/crm.db"),
        outbox_dir=g("OUTBOX_DIR", "data/outbox"),
        demo_ui=g("DEMO_UI", "1") not in ("0", "false", "False", ""),
    )

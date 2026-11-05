"""CRM SIMULADO sobre SQLite.

No es un CRM real. Imita lo esencial que haría uno (HubSpot, Pipedrive, Zoho...):
contactos con upsert idempotente por email, enriquecimiento, bitácora de actividades
y cola de correos. Para pasar a un CRM real se reemplaza esta clase por otra que
implemente los mismos métodos (ver README, sección "Cómo pasar a un CRM real").
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .models import Enrichment, LeadIn

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  name TEXT, company TEXT, job_title TEXT, country TEXT, source TEXT, message TEXT,
  status TEXT NOT NULL DEFAULT 'received',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enrichments (
  contact_id INTEGER PRIMARY KEY REFERENCES contacts(id),
  data TEXT NOT NULL, tier TEXT NOT NULL, score INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  type TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  step INTEGER NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
  send_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | failed | cancelled
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, sent_at TEXT,
  UNIQUE(contact_id, step)                  -- idempotencia: no se duplica un paso
);
CREATE TABLE IF NOT EXISTS failed_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER, stage TEXT NOT NULL, error TEXT NOT NULL, created_at TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);
"""


def now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


class SQLiteCRM:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if path == ":memory:" else None
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._mem or sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if not self._mem:
                conn.close()

    # ---------------------------------------------------------------- contactos
    def upsert_contact(self, lead: LeadIn) -> tuple[int, bool]:
        """Crea o actualiza por email. Devuelve (contact_id, es_nuevo)."""
        ts = now_iso()
        with self._conn() as c:
            row = c.execute("SELECT id FROM contacts WHERE email=?", (lead.email,)).fetchone()
            if row:
                c.execute(
                    "UPDATE contacts SET name=?, company=COALESCE(NULLIF(?,''),company), job_title=COALESCE(NULLIF(?,''),job_title),"
                    " country=COALESCE(NULLIF(?,''),country), updated_at=? WHERE id=?",
                    (lead.name, lead.company, lead.job_title, lead.country, ts, row["id"]),
                )
                # El mensaje ORIGINAL se conserva; el nuevo queda en la bitácora (no se pierde información).
                if lead.message:
                    c.execute("INSERT INTO activities(contact_id,type,detail,created_at) VALUES(?,?,?,?)",
                              (row["id"], "lead_resubmitted", lead.message[:500], ts))
                return row["id"], False
            cur = c.execute(
                "INSERT INTO contacts(email,name,company,job_title,country,source,message,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?, 'received', ?, ?)",
                (lead.email, lead.name, lead.company, lead.job_title, lead.country, lead.source, lead.message, ts, ts),
            )
            return int(cur.lastrowid), True

    def get_contact(self, contact_id: int) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
            return dict(row) if row else None

    def get_contact_by_email(self, email: str) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM contacts WHERE email=?", (email.lower(),)).fetchone()
            return dict(row) if row else None

    def set_status(self, contact_id: int, status: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE contacts SET status=?, updated_at=? WHERE id=?", (status, now_iso(), contact_id))

    # ------------------------------------------------------------ enriquecimiento
    def save_enrichment(self, contact_id: int, e: Enrichment) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO enrichments(contact_id,data,tier,score,created_at) VALUES(?,?,?,?,?)"
                " ON CONFLICT(contact_id) DO UPDATE SET data=excluded.data, tier=excluded.tier,"
                " score=excluded.score, created_at=excluded.created_at",
                (contact_id, e.model_dump_json(), e.tier, e.priority_score, now_iso()),
            )

    def get_enrichment(self, contact_id: int) -> Optional[Enrichment]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM enrichments WHERE contact_id=?", (contact_id,)).fetchone()
            return Enrichment.model_validate_json(row["data"]) if row else None

    # ----------------------------------------------------------------- actividad
    def log_activity(self, contact_id: int, type_: str, detail: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO activities(contact_id,type,detail,created_at) VALUES(?,?,?,?)",
                (contact_id, type_, detail, now_iso()),
            )

    def activities(self, contact_id: int) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM activities WHERE contact_id=? ORDER BY id", (contact_id,))]

    # ------------------------------------------------------------- cola de correos
    def enqueue_email(self, contact_id: int, step: int, subject: str, body: str, send_at: datetime) -> bool:
        """True si se encoló; False si ese paso ya existía (idempotente)."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO email_queue(contact_id,step,subject,body,send_at) VALUES(?,?,?,?,?)",
                (contact_id, step, subject, body, now_iso(send_at)),
            )
            return cur.rowcount == 1

    def due_emails(self, now: datetime) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT q.*, c.email AS to_email, c.name AS to_name FROM email_queue q JOIN contacts c ON c.id=q.contact_id"
                " WHERE q.status='pending' AND q.send_at<=? ORDER BY q.send_at, q.id",
                (now_iso(now),),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_email(self, queue_id: int, status: str, error: str = "", bump_attempt: bool = False) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE email_queue SET status=?, last_error=?, attempts=attempts+?, sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END WHERE id=?",
                (status, error, 1 if bump_attempt else 0, status, now_iso(), queue_id),
            )

    def cancel_pending_emails(self, contact_id: int) -> int:
        with self._conn() as c:
            cur = c.execute("UPDATE email_queue SET status='cancelled' WHERE contact_id=? AND status='pending'", (contact_id,))
            return cur.rowcount

    def emails_for(self, contact_id: int) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM email_queue WHERE contact_id=? ORDER BY step", (contact_id,))]

    # --------------------------------------------------------------- fallos / vista
    def record_failure(self, contact_id: Optional[int], stage: str, error: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO failed_jobs(contact_id,stage,error,created_at) VALUES(?,?,?,?)", (contact_id, stage, error[:500], now_iso()))

    def unresolved_failures(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM failed_jobs WHERE resolved=0 ORDER BY id")]

    def resolve_failures(self, contact_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE failed_jobs SET resolved=1 WHERE contact_id=?", (contact_id,))

    def list_leads(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT c.id, c.email, c.name, c.company, c.status, e.tier, e.score FROM contacts c"
                " LEFT JOIN enrichments e ON e.contact_id=c.id ORDER BY COALESCE(e.score,-1) DESC, c.id"
            ).fetchall()
            return [dict(r) for r in rows]

    def reset(self) -> None:
        """Borra todos los datos del demo (solo se usa desde /demo/reset)."""
        with self._conn() as c:
            for t in ("failed_jobs", "email_queue", "activities", "enrichments", "contacts"):
                c.execute(f"DELETE FROM {t}")

    def stats(self) -> dict[str, Any]:
        with self._conn() as c:
            return {
                "contacts": c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
                "emails_pending": c.execute("SELECT COUNT(*) FROM email_queue WHERE status='pending'").fetchone()[0],
                "emails_sent": c.execute("SELECT COUNT(*) FROM email_queue WHERE status='sent'").fetchone()[0],
                "emails_failed": c.execute("SELECT COUNT(*) FROM email_queue WHERE status='failed'").fetchone()[0],
                "failures_open": c.execute("SELECT COUNT(*) FROM failed_jobs WHERE resolved=0").fetchone()[0],
            }

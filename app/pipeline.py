"""Orquestador del flujo de punta a punta.

Etapas y estado del contacto:
  received  -> (LLM)   enriched  -> (secuencia) sequenced -> (resumen) briefed
                  \\__ cualquier fallo: status = failed:<etapa> + fila en failed_jobs

Decisión de diseño: PRIMERO se guarda el lead crudo en el CRM, DESPUÉS se enriquece.
Así, si el LLM o la red fallan, el lead NO se pierde y se puede reanudar con `resume`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .crm import SQLiteCRM
from .email_sender import Sender
from .llm import Enricher
from .models import Enrichment, LeadIn
from .retry import PermanentError, with_retries
from .sequences import schedule

log = logging.getLogger("pipeline")
MAX_EMAIL_ATTEMPTS = 3  # intentos totales por correo (entre corridas) antes de marcarlo 'failed'


class Pipeline:
    def __init__(self, crm: SQLiteCRM, enricher: Enricher, sender: Sender, seller_dir: str = "data/seller_inbox",
                 sleep=None):
        self.crm, self.enricher, self.sender = crm, enricher, sender
        self.seller_dir = Path(seller_dir)
        self.seller_dir.mkdir(parents=True, exist_ok=True)
        self._sleep = sleep  # inyectable para tests (evita esperar de verdad)

    # ------------------------------------------------------------------ entrada
    def ingest(self, lead: LeadIn) -> dict:
        """Etapas 1-5. Nunca lanza excepción por fallos de etapas: los registra y devuelve el estado."""
        contact_id, is_new = self.crm.upsert_contact(lead)
        self.crm.log_activity(contact_id, "lead_received", f"source={lead.source} new={is_new}")
        # Siempre se procesa el REGISTRO GUARDADO (no el payload nuevo): así un reenvío con un
        # mensaje pobre no degrada el enriquecimiento hecho con el mensaje original.
        result = self._run_from(contact_id, self._lead_from_db(contact_id))
        result["is_new"] = is_new
        return result

    def _lead_from_db(self, contact_id: int) -> LeadIn:
        c = self.crm.get_contact(contact_id)
        if not c:
            raise KeyError(contact_id)
        return LeadIn(name=c["name"], email=c["email"], company=c["company"] or "", job_title=c["job_title"] or "",
                      country=c["country"] or "", message=c["message"] or "", source=c["source"] or "web_form")

    def resume(self, contact_id: int) -> dict:
        """Reanuda un lead que quedó a medias (p. ej. el LLM estaba caído)."""
        return self._run_from(contact_id, self._lead_from_db(contact_id))

    # -------------------------------------------------------------- etapas internas
    def _run_from(self, contact_id: int, lead: LeadIn) -> dict:
        stage = "enrich"
        try:
            e = self.crm.get_enrichment(contact_id)
            if e is None:
                e = self._enrich(contact_id, lead)

            stage = "sequence"
            created = schedule(self.crm, contact_id, lead, e)
            self.crm.set_status(contact_id, "sequenced")
            self.crm.log_activity(contact_id, "sequence_scheduled", f"nuevos_pasos={created} tier={e.tier}")

            stage = "seller_brief"
            brief_path = self._write_seller_brief(contact_id, lead, e)
            self.crm.set_status(contact_id, "briefed")
            self.crm.log_activity(contact_id, "seller_notified", brief_path)
            self.crm.resolve_failures(contact_id)
            return {"contact_id": contact_id, "status": "briefed", "tier": e.tier, "score": e.priority_score,
                    "enrichment_source": e.enrichment_source, "seller_brief_file": brief_path}
        except Exception as exc:  # noqa: BLE001 - frontera del flujo: registrar y seguir
            log.error("falló la etapa %s para contacto %s: %s", stage, contact_id, exc)
            log.debug("detalle", exc_info=True)
            self.crm.record_failure(contact_id, stage, f"{type(exc).__name__}: {exc}")
            self.crm.set_status(contact_id, f"failed:{stage}")
            self.crm.log_activity(contact_id, "stage_failed", f"{stage}: {exc}")
            return {"contact_id": contact_id, "status": f"failed:{stage}", "error": str(exc)}

    def _enrich(self, contact_id: int, lead: LeadIn) -> Enrichment:
        kwargs = {"sleep": self._sleep} if self._sleep else {}
        # Los proveedores reales ya reintentan por dentro; este envoltorio cubre fallos de red inesperados.
        e = with_retries(lambda: self.enricher.enrich(lead), attempts=2, base_delay=0.5, label="enrich", **kwargs)
        self.crm.save_enrichment(contact_id, e)
        self.crm.set_status(contact_id, "enriched")
        self.crm.log_activity(contact_id, "enriched", f"{e.tier} {e.priority_score} via {e.enrichment_source}")
        return e

    def _write_seller_brief(self, contact_id: int, lead: LeadIn, e: Enrichment) -> str:
        pains = "\n".join(f"- {p}" for p in e.pain_points) or "- (no se detectaron)"
        talk = "\n".join(f"- {t}" for t in e.talking_points) or "- (sin puntos)"
        text = (
            f"# Lead {e.tier.upper()} ({e.priority_score}/100): {lead.name} — {lead.company or 'sin empresa'}\n\n"
            f"**Contacto:** {lead.email} · {lead.job_title or 'cargo no indicado'} · {lead.country or 'país no indicado'}\n"
            f"**Industria:** {e.industry} · **Tamaño:** {e.company_size_band} · **Cargo:** {e.seniority}\n"
            f"**Sistema que usa:** {e.erp_or_system_mentioned or 'no mencionado'}\n\n"
            f"## Por qué importa\n{e.fit_reasoning}\n\n## Dolores detectados\n{pains}\n\n"
            f"## Qué decirle\n{talk}\n\n## Resumen listo para actuar\n{e.seller_brief}\n\n"
            f"_Mensaje original del lead:_ \"{lead.message or '(vacío)'}\"\n"
            f"_Enriquecido por:_ {e.enrichment_source} (revisar antes de contactar; puede contener errores)\n"
        )
        path = self.seller_dir / f"lead_{contact_id}_{e.tier}.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    # ------------------------------------------------------------ envío de correos
    def run_due(self, now: Optional[datetime] = None) -> dict:
        """Envía los correos vencidos. Pensado para correr cada pocos minutos (cron / worker)."""
        now = now or datetime.now(timezone.utc)
        sent = failed = retried = 0
        kwargs = {"sleep": self._sleep} if self._sleep else {}
        for item in self.crm.due_emails(now):
            qid = item["id"]
            try:
                with_retries(
                    lambda: self.sender.send(to_email=item["to_email"], to_name=item["to_name"] or "",
                                             subject=item["subject"], body=item["body"],
                                             tag=f"c{item['contact_id']}_s{item['step']}"),
                    attempts=3, base_delay=0.5, label=f"email c{item['contact_id']} s{item['step']}", **kwargs,
                )
                self.crm.mark_email(qid, "sent")
                self.crm.log_activity(item["contact_id"], "email_sent", f"paso {item['step']}")
                sent += 1
            except Exception as exc:  # noqa: BLE001
                total = item["attempts"] + 1
                permanent = isinstance(exc, PermanentError) or total >= MAX_EMAIL_ATTEMPTS
                self.crm.mark_email(qid, "failed" if permanent else "pending", error=str(exc)[:300], bump_attempt=True)
                self.crm.log_activity(item["contact_id"], "email_error", f"paso {item['step']}: {exc}")
                if permanent:
                    self.crm.record_failure(item["contact_id"], "email", f"paso {item['step']}: {exc}")
                    failed += 1
                else:
                    retried += 1  # queda 'pending': se reintenta en la próxima corrida
        return {"sent": sent, "failed": failed, "will_retry": retried}

    def mark_replied(self, email: str) -> dict:
        """Si el lead responde, se frena la secuencia (nadie quiere 3 correos tras contestar)."""
        c = self.crm.get_contact_by_email(email)
        if not c:
            raise KeyError(email)
        cancelled = self.crm.cancel_pending_emails(c["id"])
        self.crm.set_status(c["id"], "replied")
        self.crm.log_activity(c["id"], "lead_replied", f"correos_cancelados={cancelled}")
        return {"contact_id": c["id"], "cancelled": cancelled}

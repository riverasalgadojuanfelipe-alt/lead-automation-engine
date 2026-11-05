"""API HTTP (FastAPI). Arranque: uvicorn app.main:app --reload"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .config import Settings, load_settings
from .crm import SQLiteCRM
from .email_sender import build_sender
from .llm import build_enricher
from .models import LeadIn
from .pipeline import Pipeline
from .security import verify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


def create_app(settings: Optional[Settings] = None, pipeline: Optional[Pipeline] = None) -> FastAPI:
    settings = settings or load_settings()
    if pipeline is None:
        pipeline = Pipeline(SQLiteCRM(settings.db_path), build_enricher(settings), build_sender(settings))
    app = FastAPI(title="Lead enrichment & outreach (demo)", version="0.1.0")

    @app.get("/health")
    def health():
        return {"ok": True, "llm": pipeline.enricher.name, "email": pipeline.sender.name, **pipeline.crm.stats()}

    @app.post("/webhooks/lead")
    async def lead_webhook(request: Request, x_signature: Optional[str] = Header(default=None)):
        body = await request.body()
        if len(body) > 20_000:
            raise HTTPException(413, "payload demasiado grande")
        if not verify(settings.webhook_secret, body, x_signature):
            raise HTTPException(401, "firma inválida")
        try:
            lead = LeadIn.model_validate(json.loads(body))
        except (json.JSONDecodeError, ValidationError) as exc:
            # 422: el remitente no debe reintentar el mismo payload roto
            raise HTTPException(422, f"payload inválido: {exc}") from exc
        # Aquí corre síncrono para que el demo se vea de una vez. En producción: responder 202
        # y procesar en una cola/worker (ver README, sección "Producción").
        return pipeline.ingest(lead)

    @app.get("/leads")
    def leads():
        return pipeline.crm.list_leads()

    @app.get("/leads/{contact_id}")
    def lead_detail(contact_id: int):
        c = pipeline.crm.get_contact(contact_id)
        if not c:
            raise HTTPException(404, "no existe")
        e = pipeline.crm.get_enrichment(contact_id)
        return {"contact": c, "enrichment": e.model_dump() if e else None,
                "emails": pipeline.crm.emails_for(contact_id), "activity": pipeline.crm.activities(contact_id)}

    @app.post("/leads/{contact_id}/resume")
    def resume(contact_id: int):
        try:
            return pipeline.resume(contact_id)
        except KeyError:
            raise HTTPException(404, "no existe")

    @app.post("/leads/replied")
    def replied(payload: dict):
        try:
            return pipeline.mark_replied(str(payload.get("email", "")))
        except KeyError:
            raise HTTPException(404, "no existe")

    @app.post("/jobs/run-due")
    def run_due(days_ahead: float = 0):
        """Envía los correos vencidos. `days_ahead` solo sirve para simular el paso del tiempo en el demo."""
        return pipeline.run_due(datetime.now(timezone.utc) + timedelta(days=days_ahead))

    @app.get("/failures")
    def failures():
        return pipeline.crm.unresolved_failures()

    # ------------------------------------------------------------------ pantalla de demo
    # Solo para uso local: NO exige firma. Se apaga con DEMO_UI=0.
    static_index = Path(__file__).parent / "static" / "index.html"

    def _demo_on() -> None:
        if not settings.demo_ui:
            raise HTTPException(404, "demo desactivado (DEMO_UI=0)")

    def _detail(contact_id: int) -> dict:
        c = pipeline.crm.get_contact(contact_id)
        if not c:
            raise HTTPException(404, "no existe")
        e = pipeline.crm.get_enrichment(contact_id)
        briefs = sorted(pipeline.seller_dir.glob(f"lead_{contact_id}_*.md"), key=lambda p: p.stat().st_mtime)
        return {
            "contact": c,
            "enrichment": e.model_dump() if e else None,
            "emails": pipeline.crm.emails_for(contact_id),
            "activity": pipeline.crm.activities(contact_id),
            "seller_brief_md": briefs[-1].read_text(encoding="utf-8") if briefs else None,
            "engine": {"llm": pipeline.enricher.name, "email": pipeline.sender.name},
        }

    @app.get("/", include_in_schema=False)
    def demo_page():
        _demo_on()
        return FileResponse(static_index)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        from fastapi.responses import Response
        return Response(status_code=204)

    @app.post("/demo/lead")
    def demo_lead(lead: LeadIn):
        _demo_on()
        result = pipeline.ingest(lead)
        return {"result": result, **_detail(result["contact_id"])}

    @app.get("/demo/lead/{contact_id}")
    def demo_lead_detail(contact_id: int):
        _demo_on()
        return _detail(contact_id)

    @app.post("/demo/advance")
    def demo_advance(days: float = 3):
        _demo_on()
        return pipeline.run_due(datetime.now(timezone.utc) + timedelta(days=days))

    @app.post("/demo/replied")
    def demo_replied(payload: dict):
        _demo_on()
        try:
            return pipeline.mark_replied(str(payload.get("email", "")))
        except KeyError:
            raise HTTPException(404, "no existe")

    @app.post("/demo/resume/{contact_id}")
    def demo_resume(contact_id: int):
        _demo_on()
        return pipeline.resume(contact_id)

    @app.post("/demo/reset")
    def demo_reset():
        _demo_on()
        pipeline.crm.reset()
        return {"ok": True}

    return app


app = create_app() if __name__ != "__main__" else None

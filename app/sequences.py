"""Secuencias de correo personalizadas según el perfil enriquecido.

Las plantillas son EJEMPLOS míos (borradores). Las afirmaciones sobre el producto salen
del sitio público de Spexs (conexión directa a la base de datos, sin migraciones ni costos
de implementación, despliegue en horas). No incluyen precios, clientes ni métricas.
Se cancelan solas si el lead responde (ver `mark_replied` en pipeline.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .models import Enrichment, LeadIn

# Días de espera (desde la entrada del lead) por prioridad. Supuesto: más prioridad, más toques.
CADENCE = {
    "hot": [0, 2, 5],
    "warm": [0, 4, 9],
    "cold": [0, 7],
}
# Qué plantilla usa cada toque: los fríos reciben solo la 1ª y la de cierre.
TEMPLATE_FOR_STEP = {
    "hot": [1, 2, 3],
    "warm": [1, 2, 3],
    "cold": [1, 3],
}

FOOTER = "\n\n—\nSi prefieres no recibir más correos, responde BAJA y te saco de la lista."


def _first_name(name: str) -> str:
    return (name.strip().split() or ["hola"])[0].capitalize()


def _opening_pain(e: Enrichment) -> str | None:
    return e.pain_points[0].lower() if e.pain_points else None


def _system_line(e: Enrichment) -> str:
    erp = e.erp_or_system_mentioned
    if erp in ("SAP Business One", "QuickBooks"):
        return f"Spexs se conecta directo a {erp}, sin migraciones ni costos de implementación."
    if erp and erp != "Excel (sin ERP)":
        return f"Cuéntame cómo sacan hoy los datos de {erp}; Spexs conecta ERP, CRM, POS y contabilidad en una sola capa."
    return "Spexs conecta tu ERP, CRM, POS y contabilidad en una sola capa de datos que se actualiza cada minuto."


def render_sequence(lead: LeadIn, e: Enrichment) -> list[tuple[int, str, str, int]]:
    """Devuelve [(paso, asunto, cuerpo, dias_de_espera), ...]."""
    nombre = _first_name(lead.name)
    empresa = lead.company or "tu empresa"
    pain = _opening_pain(e)
    sys_line = _system_line(e)
    subject_topic = pain or "tus reportes y datos de negocio"
    interest = (
        f"Vi que te interesa resolver algo como esto: {pain}." if pain
        else "Quedó claro que quieres más visibilidad de tu negocio."
    )

    steps = {
        1: (
            f"{nombre}, sobre {subject_topic} en {empresa}",
            f"Hola {nombre},\n\nGracias por escribirnos. {interest} {sys_line}\n\n"
            f"¿Te sirve una llamada de 20 minutos esta semana para ver si aplica a {empresa}?",
        ),
        2: (
            f"Re: {subject_topic} — ¿lo vemos en 20 minutos?",
            f"Hola {nombre},\n\nTe escribo de nuevo por si el primer correo se perdió. "
            f"La idea es simple: que {empresa} vea sus números al día sin depender de exportes manuales. "
            f"Se despliega en horas, no en semanas.\n\n¿Qué día te queda mejor para una llamada corta?",
        ),
        3: (
            f"Cierro el hilo, {nombre}",
            f"Hola {nombre},\n\nNo quiero llenarte la bandeja. Si ahora no es el momento, sin problema: "
            f"respóndeme con 'más adelante' y te escribo en unos meses. Si sí lo es, agendamos y listo.",
        ),
    }
    out = []
    for step_no, (days, tpl) in enumerate(zip(CADENCE[e.tier], TEMPLATE_FOR_STEP[e.tier]), start=1):
        subject, body = steps[tpl]
        out.append((step_no, subject, body + FOOTER, days))
    return out


def schedule(crm, contact_id: int, lead: LeadIn, e: Enrichment, start: Optional[datetime] = None) -> int:
    """Encola la secuencia. Devuelve cuántos pasos NUEVOS se encolaron (idempotente)."""
    from datetime import timezone

    start = start or datetime.now(timezone.utc)
    created = 0
    for step, subject, body, days in render_sequence(lead, e):
        if crm.enqueue_email(contact_id, step, subject, body, start + timedelta(days=days)):
            created += 1
    return created

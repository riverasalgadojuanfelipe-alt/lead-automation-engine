# Lead Engine

Motor de automatización de leads: recibe un lead por webhook, lo valida, lo enriquece con un LLM, lo guarda en un CRM, programa una secuencia de correos según su prioridad y le entrega al vendedor un resumen listo para actuar.

Construido con **Python, FastAPI y SQLite**. Es un proyecto de portafolio: el flujo completo funciona y está probado; el LLM, el CRM y el correo están detrás de interfaces y, por defecto, son simulados (ver [Qué es real y qué es simulado](#qué-es-real-y-qué-es-simulado)).

## Qué hace

```
Formulario / webhook
        │  POST /webhooks/lead
        ▼
1. Validar firma HMAC y payload (pydantic)
2. Guardar el lead crudo en el CRM (upsert por email → idempotente)
3. Enriquecer con LLM → JSON validado: industria, tamaño, cargo,
   dolores, score 0-100, tier y resumen
   (si falla: reintentos → reglas de respaldo)
4. Programar secuencia de correos según el tier
   hot: días 0-2-5 · warm: 0-4-9 · cold: 0-7
5. Resumen para el vendedor: quién es, por qué importa, qué decirle
```

Aparte:

- `POST /jobs/run-due` envía los correos que ya vencieron (pensado para un cron o worker).
- `POST /leads/replied` cancela el resto de la secuencia si el lead responde.
- Si una etapa falla, el lead queda en `failed:<etapa>`, se registra en `failed_jobs` y se reanuda con `/leads/{id}/resume`.

## Decisiones de diseño

| Decisión | Por qué |
|---|---|
| Guardar el lead crudo **antes** de enriquecer | Si el LLM o la red fallan, el lead no se pierde. |
| Upsert por email y `UNIQUE(contacto, paso)` | Los webhooks se reenvían. Sin idempotencia habría contactos y correos duplicados. |
| Validar siempre la salida del LLM | Puede devolver JSON roto o un score fuera de rango. Se valida el esquema, se intenta reparar una vez y, si sigue mal, se usan reglas de respaldo. |
| El lead viaja al LLM como dato | El mensaje del lead es texto no confiable (puede decir "ignora tus instrucciones"). El prompt de sistema lo declara dato. Mitiga el riesgo de prompt injection, no lo elimina. |
| Reprocesar usa el registro guardado | Un reenvío con un mensaje pobre no debe degradar el score de un lead bueno. Hay un test que lo cubre. |
| Cola de correos en base de datos + `run-due` | Separar "decidir qué enviar" de "enviar" permite reintentar, pausar y cancelar. |
| Reintentos con backoff y jitter | Los errores transitorios (429, 5xx) se reintentan; los permanentes (400, 401) no. |
| Detener la secuencia si el lead responde | Nadie quiere el tercer correo después de haber contestado. |

## Qué es real y qué es simulado

| Pieza | Estado | Detalle |
|---|---|---|
| Webhook, validación, firma HMAC | Real | Probado con tests y contra un servidor uvicorn real. |
| Idempotencia, reintentos, cola, reanudación | Real | 24 tests automáticos. |
| Enriquecimiento con LLM | Simulado por defecto | `MockLLM` usa reglas deterministas. Los proveedores Anthropic y OpenAI están implementados por HTTP y probados solo con respuestas simuladas (`httpx.MockTransport`), no contra la API real. |
| CRM | Simulado | SQLite con el comportamiento esencial de un CRM. No hay conexión a HubSpot, Pipedrive o Zoho. |
| Envío de correo | Simulado por defecto | Escribe archivos en `data/outbox/`. Existe un sender SMTP que no se probó contra un servidor real. |
| Scoring | Supuesto | Pesos: industria 25, ERP compatible 25, cargo 20, tamaño 15, dolor explícito 15. En producción se calibrarían con datos reales de cierre. |
| Resumen al vendedor | Real (archivo) | Se escribe en `data/seller_inbox/`. No hay integración con Slack ni correo. |

## Cómo correrlo

Requiere Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

**Demo en consola** (sin servidor ni claves):

```bash
python -m app.cli demo             # flujo completo con leads de ejemplo
python -m app.cli demo --chaos     # simula una caída del LLM y la recuperación
```

**Demo visual en el navegador:**

```bash
uvicorn app.main:app --port 8000
# abre http://localhost:8000/
```

Escribes un lead (o eliges un ejemplo) y ves lo que recibiría el vendedor y la secuencia de correos. Puedes avanzar el reloj, marcar que el lead respondió y reiniciar. Es solo para uso local: no exige firma. Se desactiva con `DEMO_UI=0` en `.env`.

**Con servidor real (webhook):**

```bash
uvicorn app.main:app --port 8000                       # terminal 1
python scripts/send_lead.py --index 0                  # terminal 2
curl localhost:8000/leads                              # CRM ordenado por prioridad
curl -X POST "localhost:8000/jobs/run-due?days_ahead=3"  # simula 3 días después
```

Documentación interactiva de la API en `http://localhost:8000/docs`.

**Tests:**

```bash
pytest -q
```

**Con un LLM real (opcional):** en `.env`:

```
LLM_PROVIDER=anthropic     # o openai
LLM_API_KEY=<tu clave>
LLM_MODEL=<modelo disponible en tu cuenta>
```

Nunca subas `.env` al repositorio. Si el LLM falla o devuelve basura, el sistema usa las reglas de respaldo y lo deja marcado en `enrichment_source`.

## Estructura

| Archivo | Responsabilidad |
|---|---|
| `app/main.py` | API: webhook, listado, `run-due`, `replied`, `resume` |
| `app/security.py` | Firma HMAC-SHA256 |
| `app/models.py` | Esquemas pydantic del lead y del enriquecimiento |
| `app/llm.py` | `MockLLM`, `AnthropicLLM`, `OpenAILLM` y reglas de respaldo |
| `app/crm.py` | CRM en SQLite: contactos, enriquecimiento, actividad, cola y fallos |
| `app/sequences.py` | Plantillas y cadencia por tier |
| `app/email_sender.py` | Envío a archivo o por SMTP |
| `app/pipeline.py` | Orquestador por etapas, con estado y reanudación |
| `app/retry.py` | Backoff exponencial con jitter |
| `app/cli.py` | Demo en consola |
| `docs/EMAIL_AUTH.md` | Notas sobre SPF, DKIM y DMARC |

## Producción: qué vigilar

El demo no incluye monitoreo. En producción vigilaría:

- **Métricas:** leads por hora, % enriquecidos con LLM vs. reglas de respaldo, latencia y costo por lead, correos enviados y fallidos, `failed_jobs` abiertos.
- **Alertas:** fallos abiertos durante demasiado tiempo, alta tasa de reglas de respaldo (señal de que el proveedor LLM falla), cola de correos que no baja, rebotes y quejas.
- **Logs estructurados** con `contact_id` en cada línea.
- **Cola real** (por ejemplo Redis con un worker) y dead-letter queue; el webhook solo valida, encola y responde 202.
- **Calidad de la IA:** comparar los scores con lo que el vendedor hizo realmente.

## Pasar a un CRM real

`SQLiteCRM` expone `upsert_contact`, `save_enrichment`, `log_activity`, `enqueue_email` y otros. Para HubSpot, Pipedrive o Zoho se escribe otra clase con esos métodos que llame a su API: upsert por email, propiedades personalizadas para score, tier y resumen, backoff ante 429, y credenciales en variables de entorno.

## Siguientes pasos

- Integración con WhatsApp y calendario.
- Cola real con worker y respuesta 202.
- Calibrar el score con datos históricos de cierre.
- Pruebas de entregabilidad con dominio propio (SPF, DKIM, DMARC y warm-up).
- Consentimiento y baja real de datos personales (hoy solo hay una línea "responde BAJA").

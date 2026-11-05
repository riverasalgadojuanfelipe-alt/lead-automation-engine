# Motor de enriquecimiento y outreach de leads (proyecto de portafolio)

Mini proyecto construido para preparar la entrevista del rol **Automation Engineer – Business Operations** de Spexs AI.
Simula el trabajo del rol: que el equipo comercial reciba leads **ya trabajados** (investigados, priorizados y con correos listos) sin hacer nada a mano.

> **Aviso de honestidad (léelo antes de presentarlo).** Esto es un demo de ~3 horas, no un sistema en producción.
> La tabla "Qué es real y qué es simulado" más abajo dice exactamente qué se probó y qué no. Úsala tal cual en la entrevista.

---

## 1. Qué hace

```
 ┌────────────┐   POST /webhooks/lead    ┌───────────────────────────────────────────┐
 │ Formulario │ ───────────────────────► │ 1. Validar firma HMAC + payload (pydantic) │
 │  o webhook │                          └───────────────────┬───────────────────────┘
 └────────────┘                                              ▼
                                          ┌───────────────────────────────────────────┐
                                          │ 2. Guardar lead CRUDO en el CRM (upsert    │
                                          │    por email → idempotente)  [received]    │
                                          └───────────────────┬───────────────────────┘
                                                              ▼
                                          ┌───────────────────────────────────────────┐
                                          │ 3. Enriquecer con LLM → JSON validado:     │
                                          │    industria, tamaño, cargo, dolores,      │
                                          │    score 0-100, tier, resumen  [enriched]  │
                                          │    (falla → reintentos → reglas de respaldo)│
                                          └───────────────────┬───────────────────────┘
                                                              ▼
                                          ┌───────────────────────────────────────────┐
                                          │ 4. Encolar secuencia de correos según tier │
                                          │    hot: día 0-2-5 · warm: 0-4-9 · cold: 0-7│
                                          │                              [sequenced]   │
                                          └───────────────────┬───────────────────────┘
                                                              ▼
                                          ┌───────────────────────────────────────────┐
                                          │ 5. Resumen para el vendedor: quién es, por │
                                          │    qué importa, qué decirle   [briefed]    │
                                          └───────────────────────────────────────────┘

   Aparte (cron / worker cada pocos minutos):  POST /jobs/run-due → envía los correos que ya vencieron
   Si el lead responde:                         POST /leads/replied → se cancela el resto de la secuencia
   Cualquier etapa que falle:                   status "failed:<etapa>" + fila en failed_jobs → /leads/{id}/resume
```

### Decisiones de diseño (y por qué)

| Decisión | Por qué |
|---|---|
| **Python + FastAPI + SQLite** | Python es lo que mejor domino para APIs y LLMs; FastAPI valida con pydantic y da el webhook en pocas líneas; SQLite no necesita instalar nada y se demuestra en vivo. |
| **Guardar el lead crudo ANTES de enriquecer** | Si el LLM o la red fallan, el lead no se pierde. Un lead perdido es dinero perdido. |
| **Upsert por email + `UNIQUE(contacto, paso)`** | Los webhooks se reenvían (reintentos, doble clic). Sin idempotencia habría contactos y correos duplicados. |
| **La salida del LLM siempre se valida** | Un LLM puede devolver texto extra, JSON roto o un score de 999. Se valida el esquema; si falla se repara una vez y, si sigue mal, se usan reglas de respaldo. |
| **El lead va al LLM como JSON-dato** | El mensaje del lead es texto no confiable (puede decir "ignora tus instrucciones"). Va como dato y el prompt de sistema lo declara dato. Mitiga, **no elimina**, el riesgo. |
| **Reprocesar usa el registro guardado, no el payload nuevo** | Bug real que encontré probando: un reenvío con mensaje pobre degradaba el score de un lead bueno. Hay un test que lo cubre. |
| **Cola de correos en la base + `run-due`** | Separar "decidir qué enviar" de "enviar" permite reintentar, pausar y cancelar (si responde). |
| **Reintentos con backoff + jitter, y error permanente ≠ transitorio** | 429/5xx se reintentan; 401/400 no (reintentar una API key mala solo hace ruido). |
| **Se detiene la secuencia si el lead responde** | Nadie quiere el correo 3 después de haber contestado. |

---

## 2. Qué es real y qué es simulado

| Pieza | Estado | Detalle |
|---|---|---|
| Webhook, validación, firma HMAC | **Real** | Probado con tests y con `curl`/script contra un servidor uvicorn real. |
| Idempotencia, reintentos, cola, resume | **Real** | Probado con 24 tests automáticos. |
| **Enriquecimiento con LLM** | **Simulado por defecto** | `MockLLM` son **reglas deterministas, no un LLM**. Los proveedores reales (Anthropic/OpenAI) están implementados con HTTP y probados **solo contra respuestas simuladas** (`httpx.MockTransport`): **no los llamé contra la API real** porque no tengo tu clave. Se activan con `.env`. |
| **CRM** | **Simulado** | Es SQLite con el comportamiento esencial de un CRM. **No** se conecta a HubSpot/Pipedrive/Zoho. |
| **Envío de correo** | **Simulado por defecto** | Escribe archivos en `data/outbox/`. `SMTPSender` existe pero **no lo probé contra un servidor real** y el dominio de ejemplo no tiene SPF/DKIM/DMARC. |
| Secuencias y plantillas | **Borradores míos** | Las afirmaciones del producto salen del sitio público de Spexs. No inventé precios, clientes ni métricas. |
| **Scoring** | **Supuesto mío** | Pesos: industria ICP 25 + ERP compatible 25 + cargo 20 + tamaño 15 + dolor explícito 15. Los pesos no vienen de Spexs. En producción se calibrarían con datos reales de cierre. |
| Resumen al vendedor | **Real (archivo)** | Se escribe en `data/seller_inbox/`. No hay integración con Slack/email del vendedor. |
| WhatsApp / calendario | **No incluido** | La vacante los menciona; no los construí (ver "Siguientes pasos"). |

Qué sí puedes decir con confianza: *"construí el flujo completo con pruebas; el LLM, el CRM y el correo están detrás de interfaces y en el demo son simulados; cambiar cada uno es cambiar una clase."*

---

## 3. Cómo correrlo

Requiere Python 3.10+.

```bash
cd spexs-lead-engine
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

**Demo en consola (1 minuto, sin servidor ni claves):**

```bash
python -m app.cli demo             # flujo completo con 6 leads de ejemplo
python -m app.cli demo --chaos     # además simula una caída del LLM y la recuperación
```

**Demo visual en el navegador (la que conviene mostrar):**

```bash
uvicorn app.main:app --port 8000
# abre http://localhost:8000/
```
Escribes un lead (o eliges un ejemplo), pulsas **Enviar** y ves al instante lo que recibiría el vendedor y la secuencia de correos;
puedes avanzar el reloj, marcar que el lead respondió y reiniciar. La página indica arriba qué es simulado.
Solo para uso local: no exige firma. Se apaga con `DEMO_UI=0` en `.env`.

**Demo con servidor real (webhook):**

```bash
uvicorn app.main:app --port 8000                       # terminal 1
python scripts/send_lead.py --index 0                  # terminal 2: envía un lead (firmado si hay WEBHOOK_SECRET)
curl localhost:8000/leads                              # CRM ordenado por prioridad
curl -X POST "localhost:8000/jobs/run-due?days_ahead=3"  # simula que pasaron 3 días y envía el 2º correo
```
Documentación interactiva de la API: `http://localhost:8000/docs`.

**Tests:**

```bash
pytest -q        # 24 tests
```

**Con un LLM real (opcional):** edita `.env`:
```
LLM_PROVIDER=anthropic     # o openai
LLM_API_KEY=tu_clave
LLM_MODEL=el_modelo_que_tengas_disponible
```
Si el LLM falla o responde basura, el sistema cae a las reglas de respaldo y lo deja marcado en `enrichment_source`.

---

## 4. Mapa del código (para estudiar)

| Archivo | Qué hace | Qué debes poder explicar |
|---|---|---|
| `app/main.py` | API: webhook, listado, `run-due`, `replied`, `resume` | Por qué 401/422/413 y por qué el webhook procesa síncrono solo en el demo |
| `app/security.py` | Firma HMAC-SHA256 | Qué prueba una firma y por qué `compare_digest` |
| `app/models.py` | Esquemas pydantic (lead de entrada, enriquecimiento) | Validar en la frontera: todo dato externo es no confiable |
| `app/llm.py` | `MockLLM`, `AnthropicLLM`, `OpenAILLM`, reglas de respaldo | Salida estructurada, validación, reparación, prompt injection |
| `app/crm.py` | CRM SQLite: contactos, enriquecimiento, actividad, cola, fallos | Upsert idempotente, `UNIQUE(contact_id, step)` |
| `app/sequences.py` | Plantillas y cadencia por tier | Por qué más toques a los leads calientes |
| `app/email_sender.py` | Envío a archivo o SMTP | `List-Unsubscribe`, baja, por qué el demo no envía de verdad |
| `app/pipeline.py` | Orquestador por etapas con estado y `resume` | Persistir primero, fallar sin perder, reanudar |
| `app/retry.py` | Backoff exponencial + jitter | Error transitorio vs. permanente |
| `app/cli.py` | Demo en consola (+ modo `--chaos`) | Qué muestra cada paso |

---

## 5. Manejo de errores y cómo vigilarlo en producción

**Qué ya hace el demo**
- Validación de entrada (422 si el payload es inválido, 413 si es enorme, 401 si la firma no cuadra).
- Reintentos con backoff y jitter en LLM y correo; los errores permanentes (401/credenciales) no se reintentan.
- Cada fallo queda en `failed_jobs` con la etapa y el error; el contacto queda en `failed:<etapa>` y se reanuda con `/leads/{id}/resume`.
- Un correo se reintenta entre corridas hasta 3 veces y luego se marca `failed` (no se reenvía para siempre).

**Qué vigilaría en producción** (esto es lo que *haría*, no lo que construí)
1. **Métricas operativas:** leads recibidos/hora, % enriquecidos con LLM vs. con reglas de respaldo, latencia y costo por lead del LLM, correos enviados/fallidos, tamaño de `failed_jobs` abierto.
2. **Alertas:** `failed_jobs` abiertos > N durante X minutos; tasa de reglas de respaldo > umbral (señal de que el proveedor LLM falla); cola de correos que no baja; tasa de rebote o de quejas de spam.
3. **Logs estructurados** con `contact_id` en cada línea para rastrear un lead de punta a punta.
4. **Cola real** (por ejemplo Redis + worker, o la cola del proveedor cloud) con *dead-letter queue*; el webhook solo valida, encola y responde `202` rápido.
5. **Calidad de la IA:** muestrear scores y compararlos con lo que el vendedor hizo; revisar si los leads "hot" realmente avanzan.
6. **Salud de entregabilidad:** rebotes, quejas, reputación del dominio (ver `docs/EMAIL_AUTH.md`).

---

## 6. Cómo pasar a un CRM real

`SQLiteCRM` expone: `upsert_contact`, `save_enrichment`, `log_activity`, `enqueue_email`, etc. Para un CRM real (HubSpot, Pipedrive, Zoho) se escribe otra clase con esos métodos que llame a su API:
- **Upsert por email** (casi todos los CRM lo soportan; evita duplicados).
- **Propiedades personalizadas** para score, tier y resumen del enriquecimiento.
- **Respetar límites de tasa** (429 → reintento con backoff; ya lo hace `retry.py`).
- **Webhooks entrantes del CRM** (p. ej. "el lead respondió" → `mark_replied`).
- Autenticación por token/OAuth guardado en variables de entorno, nunca en el código.

---

## 7. Siguientes pasos (no incluidos)

- WhatsApp y calendario (agendado de llamada) integrados al CRM.
- Cola real + worker y respuesta `202` en el webhook.
- Calibrar el score con datos históricos de cierre.
- Pruebas de entregabilidad reales (dominio de envío con SPF/DKIM/DMARC y *warm-up*).
- Tratamiento de datos personales: consentimiento y baja real (el demo solo incluye la línea "responde BAJA").

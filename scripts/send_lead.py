"""Envía un lead de ejemplo al webhook, firmado con HMAC.

  python scripts/send_lead.py                    # usa WEBHOOK_SECRET del .env (o sin firma si está vacío)
  python scripts/send_lead.py --index 2          # elige otro lead de samples/leads.json
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import load_settings  # noqa: E402
from app.security import sign  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000/webhooks/lead")
ap.add_argument("--index", type=int, default=0)
a = ap.parse_args()

secret = load_settings().webhook_secret
leads = json.loads(Path("samples/leads.json").read_text(encoding="utf-8"))
body = json.dumps(leads[a.index], ensure_ascii=False).encode()
headers = {"X-Signature": sign(secret, body)} if secret else {}
r = httpx.post(a.url, content=body, headers=headers, timeout=30)
print(r.status_code, r.text)

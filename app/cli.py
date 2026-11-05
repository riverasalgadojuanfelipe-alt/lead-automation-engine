"""CLI para demostrar el sistema sin levantar servidor.

  python -m app.cli demo            # flujo completo con 6 leads de ejemplo
  python -m app.cli demo --chaos    # además simula una caída del LLM y la recuperación
  python -m app.cli leads           # lista leads del CRM
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import load_settings
from .crm import SQLiteCRM
from .email_sender import build_sender
from .llm import build_enricher
from .models import LeadIn
from .pipeline import Pipeline


class FlakyEnricher:
    """Envoltorio de PRUEBA: falla las N primeras llamadas para mostrar errores y recuperación."""

    def __init__(self, inner, fail_first: int):
        self.inner, self.left, self.name = inner, fail_first, inner.name

    def enrich(self, lead):
        if self.left > 0:
            self.left -= 1
            raise ConnectionError("simulado: el proveedor de LLM no responde")
        return self.inner.enrich(lead)


def h(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def cmd_demo(args) -> None:
    s = load_settings()
    if not args.keep:  # demo limpio y repetible
        for p in (Path(s.db_path), Path(s.outbox_dir), Path("data/seller_inbox")):
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    enricher = build_enricher(s)
    if args.chaos:
        enricher = FlakyEnricher(enricher, fail_first=4)  # 2 intentos x lead -> el 1º y el 2º lead fallan
    pipe = Pipeline(SQLiteCRM(s.db_path), enricher, build_sender(s), sleep=lambda _: None)

    h(f"1) Entrada de leads  (LLM={enricher.name} · correo={pipe.sender.name})")
    leads = json.loads(Path("samples/leads.json").read_text(encoding="utf-8"))
    for raw in leads:
        r = pipe.ingest(LeadIn(**raw))
        extra = f"{r.get('tier', '-')}/{r.get('score', '-')}" if "tier" in r else r.get("error", "")
        print(f"  {raw['name']:<14} {raw['email']:<42} → {r['status']:<20} {extra}  {'(duplicado: mismo email)' if not r['is_new'] else ''}")

    if args.chaos:
        h("1b) Fallos registrados (el lead NO se perdió)")
        for f in pipe.crm.unresolved_failures():
            print(f"  contacto {f['contact_id']} · etapa {f['stage']} · {f['error']}")
        h("1c) El proveedor vuelve → reanudamos desde la última etapa buena")
        for f in pipe.crm.unresolved_failures():
            print(f"  resume({f['contact_id']}) →", pipe.resume(f["contact_id"])["status"])

    h("2) CRM: leads ordenados por prioridad")
    for row in pipe.crm.list_leads():
        print(f"  #{row['id']} {row['name']:<14} {row['status']:<10} {row['tier'] or '-':<5} {row['score'] if row['score'] is not None else '-'}")

    top = pipe.crm.list_leads()[0]
    h(f"3) Resumen que recibe el vendedor (lead #{top['id']})")
    print(next(Path("data/seller_inbox").glob(f"lead_{top['id']}_*.md")).read_text(encoding="utf-8"))

    h("4) Correos: hoy (solo vencen los del día 0)")
    print(" ", pipe.run_due())
    first = sorted(Path(s.outbox_dir).glob("*.txt"))[0]
    print(f"\n  Ejemplo ({first.name}):\n" + "\n".join("    " + l for l in first.read_text(encoding="utf-8").splitlines()))

    h("5) Avanzamos el reloj +3 días (vence el 2º correo de los 'hot')")
    print(" ", pipe.run_due(datetime.now(timezone.utc) + timedelta(days=3)))

    h("6) Ana responde → se cancelan sus correos pendientes")
    print(" ", pipe.mark_replied("ana.gomez@distribuidoraandina.example"))
    print("  (+10 días)", pipe.run_due(datetime.now(timezone.utc) + timedelta(days=10)))

    h("7) Resumen final")
    print(" ", pipe.crm.stats())
    print("  Prompt injection (Sofía): revisa su score; el LLM real debe tratar el mensaje como dato.")


def cmd_leads(_args) -> None:
    s = load_settings()
    for row in SQLiteCRM(s.db_path).list_leads():
        print(row)


def main() -> None:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--chaos", action="store_true", help="simula caída del LLM y recuperación")
    d.add_argument("--keep", action="store_true", help="no borra datos previos")
    d.set_defaults(fn=cmd_demo)
    l = sub.add_parser("leads")
    l.set_defaults(fn=cmd_leads)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

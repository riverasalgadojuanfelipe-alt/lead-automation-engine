from fastapi.testclient import TestClient

from app.crm import SQLiteCRM
from app.email_sender import FileOutboxSender
from app.llm import MockLLM
from app.main import create_app
from app.pipeline import Pipeline
from tests.test_pipeline import settings

LEAD = {"name": "Ana", "email": "ana@x.co", "company": "Distribuidora", "job_title": "CFO",
        "message": "Usamos SAP Business One y Excel, 120 empleados"}


def client(tmp_path, **kw):
    p = Pipeline(SQLiteCRM(":memory:"), MockLLM(), FileOutboxSender(str(tmp_path / "o"), "v@x.co"),
                 seller_dir=str(tmp_path / "s"))
    return TestClient(create_app(settings(llm_provider="mock", webhook_secret="secreto", **kw), p))


def test_pagina_y_flujo_demo_sin_firma(tmp_path):
    c = client(tmp_path)  # aunque haya WEBHOOK_SECRET, la UI local no lo exige
    assert "Lead entra" in c.get("/").text
    d = c.post("/demo/lead", json=LEAD).json()
    assert d["result"]["status"] == "briefed" and d["enrichment"]["tier"] == "hot"
    assert d["seller_brief_md"].startswith("# Lead HOT") and len(d["emails"]) == 3
    assert c.post("/demo/advance?days=3").json()["sent"] == 2
    assert c.post("/demo/replied", json={"email": "ana@x.co"}).json()["cancelled"] == 1
    assert c.post("/demo/reset").json() == {"ok": True}
    assert c.get("/leads").json() == []


def test_demo_apagado_devuelve_404(tmp_path):
    c = client(tmp_path, demo_ui=False)
    assert c.get("/").status_code == 404
    assert c.post("/demo/lead", json=LEAD).status_code == 404
    assert c.post("/demo/reset").status_code == 404


def test_demo_valida_el_lead(tmp_path):
    assert client(tmp_path).post("/demo/lead", json={"name": "x", "email": "malo"}).status_code == 422

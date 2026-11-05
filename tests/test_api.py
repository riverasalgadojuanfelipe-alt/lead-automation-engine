import json

from fastapi.testclient import TestClient

from app.crm import SQLiteCRM
from app.email_sender import FileOutboxSender
from app.llm import MockLLM
from app.main import create_app
from app.pipeline import Pipeline
from app.security import sign
from tests.test_pipeline import settings

PAYLOAD = {"name": "Ana", "email": "ana@x.co", "company": "Tienda", "job_title": "CFO", "message": "usamos quickbooks"}


def client(tmp_path, secret=""):
    p = Pipeline(SQLiteCRM(":memory:"), MockLLM(), FileOutboxSender(str(tmp_path / "o"), "v@x.co"),
                 seller_dir=str(tmp_path / "s"))
    return TestClient(create_app(settings(llm_provider="mock", webhook_secret=secret), p))


def test_webhook_ok_y_listado(tmp_path):
    c = client(tmp_path)
    r = c.post("/webhooks/lead", json=PAYLOAD)
    assert r.status_code == 200 and r.json()["status"] == "briefed"
    assert c.get("/leads").json()[0]["email"] == "ana@x.co"
    assert c.get(f"/leads/{r.json()['contact_id']}").json()["enrichment"]["tier"] in ("hot", "warm", "cold")
    assert c.get("/health").json()["ok"] is True


def test_webhook_con_firma(tmp_path):
    c = client(tmp_path, secret="s3")
    body = json.dumps(PAYLOAD).encode()
    assert c.post("/webhooks/lead", content=body).status_code == 401
    assert c.post("/webhooks/lead", content=body, headers={"X-Signature": "0" * 64}).status_code == 401
    ok = c.post("/webhooks/lead", content=body, headers={"X-Signature": sign("s3", body)})
    assert ok.status_code == 200


def test_payload_invalido_422_y_grande_413(tmp_path):
    c = client(tmp_path)
    assert c.post("/webhooks/lead", json={"name": "x", "email": "malo"}).status_code == 422
    assert c.post("/webhooks/lead", content=b"no-json").status_code == 422
    assert c.post("/webhooks/lead", content=b"x" * 30_000).status_code == 413
    assert c.get("/leads/999").status_code == 404


def test_run_due_y_replied(tmp_path):
    c = client(tmp_path)
    c.post("/webhooks/lead", json=PAYLOAD)
    assert c.post("/jobs/run-due").json()["sent"] == 1
    assert c.post("/leads/replied", json={"email": "ana@x.co"}).status_code == 200
    assert c.post("/leads/replied", json={"email": "nadie@x.co"}).status_code == 404

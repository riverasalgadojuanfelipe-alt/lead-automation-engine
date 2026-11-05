import pytest

from app.crm import SQLiteCRM
from app.email_sender import FileOutboxSender
from app.llm import MockLLM
from app.pipeline import Pipeline


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.retry.time.sleep", lambda s: None)


@pytest.fixture
def make_pipeline(tmp_path):
    def _make(enricher=None, sender=None):
        return Pipeline(
            SQLiteCRM(":memory:"),
            enricher or MockLLM(),
            sender or FileOutboxSender(str(tmp_path / "outbox"), "ventas@example.com"),
            seller_dir=str(tmp_path / "seller"),
            sleep=lambda s: None,
        )
    return _make

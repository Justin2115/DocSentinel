from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.services import llm_service
from tests.helpers import get_test_auth_headers


def _headers(email: str, role: str = "ADMIN") -> dict[str, str]:
    db = SessionLocal()
    try:
        return get_test_auth_headers(db, email=email, role=role)
    finally:
        db.close()


def test_conversations_are_private_and_crud_is_persistent():
    client = TestClient(app)
    owner = _headers("chat-owner@example.test")
    other = _headers("chat-other@example.test")

    created = client.post("/api/conversations", headers=owner, json={"title": "  First   chat "})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert created.json()["title"] == "First chat"

    assert client.get("/api/conversations", headers=owner).json()[0]["id"] == conversation_id
    assert client.get("/api/conversations", headers=other).json() == []
    assert client.get(f"/api/conversations/{conversation_id}", headers=other).status_code == 404
    assert client.patch(
        f"/api/conversations/{conversation_id}", headers=other, json={"title": "stolen"}
    ).status_code == 404
    assert client.delete(f"/api/conversations/{conversation_id}", headers=other).status_code == 404

    renamed = client.patch(
        f"/api/conversations/{conversation_id}", headers=owner, json={"title": "Quarterly review"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Quarterly review"
    assert client.get(f"/api/conversations/{conversation_id}", headers=owner).json()["messages"] == []
    assert client.delete(f"/api/conversations/{conversation_id}", headers=owner).status_code == 204
    assert client.get(f"/api/conversations/{conversation_id}", headers=owner).status_code == 404


def test_general_chat_stream_persists_and_replays_idempotently(monkeypatch):
    def fake_general(question, history, on_delta):
        on_delta("A general ")
        on_delta("answer.")
        return SimpleNamespace(text="A general answer.", model="test-local-model", latency_ms=21)

    monkeypatch.setattr(llm_service, "generate_general_answer", fake_general)
    client = TestClient(app)
    headers = _headers("chat-stream@example.test")
    created = client.post(
        "/api/conversations", headers=headers, json={"mode": "general"}
    )
    conversation_id = created.json()["id"]
    body = {"content": "What is OCR?", "mode": "general", "request_id": "retry-123"}

    first = client.post(
        f"/api/conversations/{conversation_id}/messages/stream", headers=headers, json=body
    )
    assert first.status_code == 200
    assert "event: delta" in first.text
    assert '"text": "A general "' in first.text
    assert '"text": "answer."' in first.text
    assert "event: complete" in first.text

    replay = client.post(
        f"/api/conversations/{conversation_id}/messages/stream", headers=headers, json=body
    )
    assert replay.status_code == 200
    assert '"replayed": true' in replay.text

    detail = client.get(f"/api/conversations/{conversation_id}", headers=headers).json()
    assert detail["chat_mode"] == "general"
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "A general answer."
    assert detail["messages"][1]["model_id"] == "test-local-model"
    assert detail["messages"][1]["generation_ms"] == 21

    client.delete(f"/api/conversations/{conversation_id}", headers=headers)

"""Ollama provider behaviour: unavailable runtime, grounding, and scope safety.

Every test here stubs the HTTP transport, so no model load and no network. The
one test that uses the real installed model is skipped when Ollama is not
answering locally.
"""

import sys
import urllib.error
from pathlib import Path

import pytest

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.core.config import Settings, settings
from app.db.session import SessionLocal
from app.models.document import Document, DocumentPage, OCRResult
from app.models.user import User
from app.schemas.query import ChatQueryRequest
from app.services import llm_service
from app.services.embedding_service import set_encode_override
from app.services.rag_service import answer_question
from tests.helpers import mock_multilingual_encode


@pytest.fixture(autouse=True)
def clean_provider_cache():
    llm_service._status_cache["payload"] = None
    llm_service._status_cache["checked_at"] = 0.0
    set_encode_override(mock_multilingual_encode)
    yield
    llm_service._status_cache["payload"] = None
    set_encode_override(None)


def test_fast_llama_defaults_without_environment(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    config = Settings(_env_file=None)

    assert config.OLLAMA_MODEL == "llama3.2:3b"
    assert config.OLLAMA_GENERATE_TIMEOUT == 45.0
    assert config.OLLAMA_MAX_NEW_TOKENS == 160


class FakeOllama:
    """Records the requests a test made and replies with a canned completion."""

    def __init__(self, models=None, response="", error=None, extra=None):
        # Absent a list, the runtime has the configured model installed. Naming
        # llama3.2:3b here instead tied every one of these tests to whichever tag
        # .env happens to point at: switch the setting and the stub claims the
        # model is missing, so the fallback path gets tested by accident.
        installed = (settings.OLLAMA_MODEL or "").strip()
        self.models = list(models) if models is not None else ([installed] if installed else [])
        self.response = response
        self.error = error
        self.extra = dict(extra or {})
        self.requests: list[dict] = []

    def get(self, path, timeout):
        assert path == "/api/tags"
        return {"models": [{"name": name} for name in self.models]}

    def post(self, path, payload, timeout):
        assert path == "/api/generate"
        self.requests.append(payload)
        if self.error is not None:
            raise self.error
        return {"response": self.response, "eval_count": 40, **self.extra}

    def install(self, monkeypatch):
        monkeypatch.setattr(llm_service, "_get", self.get)
        monkeypatch.setattr(llm_service, "_post", self.post)


@pytest.fixture
def evidence_doc():
    """One isolated document whose OCR line answers a paraphrased question."""
    db = SessionLocal()
    document = Document(
        original_filename="clinic_receipt.pdf",
        stored_filename="clinic_receipt.pdf",
        file_path="uploads/clinic_receipt.pdf",
        file_type="image/png",
        status="completed",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    page = DocumentPage(document_id=document.id, page_number=1)
    db.add(page)
    db.commit()
    db.add(
        OCRResult(
            document_id=document.id,
            page_id=page.id,
            extracted_text="SELF CARE SESSION\nReading therapy $7.60\nBATH TIME $12.00\n",
        )
    )
    db.commit()
    yield document
    db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
    db.query(DocumentPage).filter(DocumentPage.document_id == document.id).delete()
    db.query(Document).filter(Document.id == document.id).delete()
    db.commit()
    db.close()


def test_status_reports_disabled_missing_model_and_installed(monkeypatch):
    FakeOllama().install(monkeypatch)
    payload = llm_service.provider_status(force=True)
    assert payload["reachable"] is True
    assert payload["model_installed"] is bool(llm_service.configured_model())

    FakeOllama(models=("other-model:1b",)).install(monkeypatch)
    payload = llm_service.provider_status(force=True)
    assert payload["model_installed"] is False
    assert "not installed" in payload["reason"]


class TestTheModelNameIsConfiguration:
    """Switching the generation model must be a config change, never a code change.

    The name arrives from OLLAMA_MODEL, so llama3.2:3b can be replaced by
    qwen3:4b with one environment value - and a switch that cannot work is
    reported as unavailable instead of quietly answered by the fallback.
    """

    BLOCK = (
        "[1] document_id=1 document=clinic_receipt.pdf page=1 source=entry\n"
        "<untrusted_document_text>\nReading therapy $7.60\n</untrusted_document_text>"
    )

    def test_any_installed_model_answers_with_its_own_name(self, monkeypatch):
        monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3:4b")
        fake = FakeOllama(
            models=("llama3.2:3b", "qwen3:4b"),
            response="Reading therapy $7.60.\nEVIDENCE: 1",
        )
        fake.install(monkeypatch)
        payload = llm_service.provider_status(force=True)
        assert payload["model"] == "qwen3:4b"
        assert payload["model_installed"] is True
        assert llm_service.is_usable() is True
        result = llm_service.generate_answer("Which activity involves reading?", [self.BLOCK])
        assert result is not None
        assert fake.requests[0]["model"] == "qwen3:4b"

    def test_a_different_tag_of_the_same_family_is_not_the_configured_model(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3:4b")
        FakeOllama(models=("qwen3:8b",)).install(monkeypatch)
        payload = llm_service.provider_status(force=True)
        assert payload["model_installed"] is False
        assert "not installed" in payload["reason"]
        assert llm_service.generate_answer("q", [self.BLOCK]) is None

    def test_an_unqualified_name_means_the_latest_tag(self, monkeypatch):
        monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3")
        FakeOllama(models=("qwen3:latest",)).install(monkeypatch)
        assert llm_service.provider_status(force=True)["model_installed"] is True


def test_reasoning_is_off_unless_it_is_asked_for(monkeypatch):
    """A reasoning model spends the token budget on its scratchpad first.

    The flag goes to every model because the alternative is worse: with thinking
    left on, qwen3-class models can return an empty answer field and the chatbot
    falls back without anyone being told why.
    """
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3:4b")
    fake = FakeOllama(
        models=("qwen3:4b",), response="Reading therapy $7.60.\nEVIDENCE: 1"
    )
    fake.install(monkeypatch)
    block = (
        "<untrusted_document_text>\nReading therapy $7.60\n</untrusted_document_text>"
    )
    assert llm_service.generate_answer("q", [block]) is not None
    assert fake.requests[0]["think"] is False

    monkeypatch.setattr(settings, "OLLAMA_THINKING", True)
    assert llm_service.generate_answer("q", [block]) is not None
    assert fake.requests[1]["think"] is True


def test_a_scratchpad_without_an_answer_is_not_mined(monkeypatch, caplog):
    fake = FakeOllama(
        response="",
        extra={
            "thinking": "The passage says $7.60, so twice that would be $15.20...",
            "eval_count": 40,
        },
    )
    fake.install(monkeypatch)
    with caplog.at_level("WARNING"):
        result = llm_service.generate_answer(
            "q", ["<untrusted_document_text>\nReading therapy $7.60\n</untrusted_document_text>"]
        )
    assert result is None
    assert "reasoning but no answer" in caplog.text


def test_the_answer_is_attributed_to_the_model_that_ran(monkeypatch):
    """The notes name the model, so they have to name the one Ollama used."""
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3")
    fake = FakeOllama(
        models=("qwen3:latest", "qwen3:4b"),
        response="Reading therapy $7.60.\nEVIDENCE: 1",
        extra={"model": "qwen3:4b", "eval_duration": 1_200_000_000},
    )
    fake.install(monkeypatch)
    result = llm_service.generate_answer(
        "q", ["<untrusted_document_text>\nReading therapy $7.60\n</untrusted_document_text>"]
    )
    assert result is not None
    assert result.model == "qwen3:4b"
    assert result.raw["eval_duration"] == 1_200_000_000
    assert result.latency_ms >= 0


def test_which_model_ran_is_logged_even_when_the_answer_is_dropped(monkeypatch, caplog):
    """The rejected generations are the ones an auditor asks about.

    "Did the configured model produce this?" has to be answerable from the backend
    log for every generation, not only for the answers that survived the gates.
    """
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "llama3.2:3b")
    fake = FakeOllama(
        response="INSUFFICIENT_EVIDENCE",
        extra={"model": "llama3.2:3b", "eval_count": 3},
    )
    fake.install(monkeypatch)
    with caplog.at_level("INFO"):
        assert (
            llm_service.generate_answer(
                "q", ["<untrusted_document_text>\nReading therapy $7.60\n</untrusted_document_text>"]
            )
            is None
        )
    assert "asked llama3.2:3b, reported llama3.2:3b" in caplog.text


def test_refused_connection_is_reported_not_raised(monkeypatch):
    def refuse(path, timeout):
        raise ConnectionRefusedError("nothing listening")

    monkeypatch.setattr(llm_service, "_get", refuse)
    payload = llm_service.provider_status(force=True)
    assert payload["reachable"] is False
    assert llm_service.is_usable() is False
    assert llm_service.generate_answer("q", ["block"]) is None


def test_generation_timeout_falls_back_to_the_caller(monkeypatch, evidence_doc):
    def timeout(path, payload, seconds):
        raise TimeoutError("generation budget exceeded")

    FakeOllama().install(monkeypatch)
    monkeypatch.setattr(llm_service, "_post", timeout)
    llm_service.provider_status(force=True)
    assert llm_service.generate_answer("q", ["block"]) is None

    # RAG still answers from evidence, and says which path produced the text.
    db = SessionLocal()
    try:
        response = answer_question(
            db, ChatQueryRequest(question="Which activity involves reading?")
        )
    finally:
        db.close()
    assert response.engine != "llm_ollama"
    assert response.has_evidence is True


def test_prompt_delimits_document_text_and_keeps_the_floor(monkeypatch, evidence_doc):
    fake = FakeOllama(response="Reading therapy $7.60 — clinic_receipt.pdf, page 1.\nEVIDENCE: 1")
    fake.install(monkeypatch)
    blocks = [
        "[1] document_id=1 document=clinic_receipt.pdf page=1 source=entry\n"
        "<untrusted_document_text>\nReading therapy $7.60\n</untrusted_document_text>"
    ]
    result = llm_service.generate_answer("Which activity involves reading?", blocks)
    assert result is not None
    prompt = fake.requests[0]["prompt"]
    assert "<untrusted_document_text>" in prompt
    assert "INSUFFICIENT_EVIDENCE" in fake.requests[0]["system"]
    assert fake.requests[0]["stream"] is False
    assert "format" not in fake.requests[0]  # this Ollama build 500s on format="text"
    assert fake.requests[0]["options"]["temperature"] == 0.0
    assert result.evidence_indices == (1,)


def test_model_answer_without_a_verbatim_quote_is_discarded(monkeypatch, evidence_doc):
    fake = FakeOllama(response="The clinic offers a discounted package.\nEVIDENCE: 1")
    fake.install(monkeypatch)
    db = SessionLocal()
    try:
        response = answer_question(
            db, ChatQueryRequest(question="Which activity involves reading?")
        )
    finally:
        db.close()
    assert response.engine != "llm_ollama"
    assert "discounted" not in response.answer


def test_grounded_model_answer_is_reported_as_local_llm(monkeypatch, evidence_doc):
    fake = FakeOllama(
        response="Reading therapy $7.60 is the activity that involves reading.\nEVIDENCE: 1"
    )
    fake.install(monkeypatch)
    db = SessionLocal()
    try:
        response = answer_question(
            db, ChatQueryRequest(question="Which activity involves reading?")
        )
    finally:
        db.close()
    assert response.engine == "llm_ollama"
    assert response.has_evidence is True
    assert "no document content left this machine" in (response.notes or "")
    assert response.sources and response.sources[0].document_id == evidence_doc.id
    assert "$7.60" in response.answer


def test_fabricated_number_is_rejected_even_when_quoting(monkeypatch, evidence_doc):
    fake = FakeOllama(response="Reading therapy costs $99.99 today.\nEVIDENCE: 1")
    fake.install(monkeypatch)
    db = SessionLocal()
    try:
        response = answer_question(
            db, ChatQueryRequest(question="Which activity involves reading?")
        )
    finally:
        db.close()
    assert response.engine != "llm_ollama"
    assert "$99.99" not in response.answer


def test_unauthorized_document_text_never_reaches_the_model(monkeypatch):
    fake = FakeOllama(response="no")
    fake.install(monkeypatch)
    from app.services import evidence_service as ev

    db = SessionLocal()
    secret = Document(
        original_filename="finance_only.pdf",
        stored_filename="finance_only.pdf",
        file_path="uploads/finance_only.pdf",
        file_type="application/pdf",
        status="completed",
        department="Finance",
    )
    db.add(secret)
    db.commit()
    db.refresh(secret)
    db.add(
        OCRResult(
            document_id=secret.id,
            extracted_text="CONFIDENTIAL LEDGER 4471\nTotal exposure $9,999,999\n",
        )
    )
    db.commit()
    outsider = User(
        name="Outsider",
        email="outsider@example.com",
        role="UPLOAD_CHECKER",
        department="HR",
        is_active=True,
    )
    db.add(outsider)
    db.commit()
    try:
        plan = ev.build_query_plan("What is the total exposure?")
        units, _diag = ev.collect_evidence(db, plan, user=outsider)
        assert units == [], "Unauthorized OCR text must not become evidence"

        response = answer_question(
            db,
            ChatQueryRequest(
                question="What is the total exposure?", document_ids=[secret.id]
            ),
            user=outsider,
        )
        assert response.engine == "unauthorized"
        assert response.has_evidence is False
        assert fake.requests == [], "Nothing may be sent to the model"
    finally:
        db.query(OCRResult).filter(OCRResult.document_id == secret.id).delete()
        db.query(Document).filter(Document.id == secret.id).delete()
        db.query(User).filter(User.id == outsider.id).delete()
        db.commit()
        db.close()


SCRATCHPAD = (
    'We are given 6 evidence passages. The question is: "Whose law governs this '
    'agreement?"\n\nWe must answer only from the numbered evidence passages (the '
    'passages are numbered 1 to 6). Passage [1] says: Reading therapy $7.60\n'
    'EVIDENCE: 1'
)


def test_an_analysis_preamble_is_not_an_answer(monkeypatch):
    """Model self-talk must not reach the user just because it quotes a passage.

    The scratchpad repeats the harness's own passage numbering, which no document
    contains, and it is verifiably not a sentence addressed to the user. The quote
    gate accepts it, so this guard is the only thing between it and the chat.
    """
    fake = FakeOllama(models=("stub:1b",), response=SCRATCHPAD)
    fake.install(monkeypatch)
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "stub:1b")
    result = llm_service.generate_answer(
        "Whose law governs this agreement?",
        ["[1] document=clinic_receipt.pdf page=1\nReading therapy $7.60"],
    )
    assert result is None


def test_a_truncated_scratchpad_is_dropped_with_a_reason(monkeypatch, caplog):
    """Budget exhausted mid-preamble: the text stops, but it still is not an answer."""
    fake = FakeOllama(
        models=("stub:1b",),
        response=(
            "First, I need to understand the user's question. The evidence passages "
            "are: [1] Reading therapy $7.60"
        ),
        extra={"eval_count": settings.OLLAMA_MAX_NEW_TOKENS, "done": True},
    )
    fake.install(monkeypatch)
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "stub:1b")
    blocks = ["[1] document=clinic_receipt.pdf page=1\nReading therapy $7.60"]
    with caplog.at_level("INFO"):
        result = llm_service.generate_answer("Whose law governs this agreement?", blocks)
    assert result is None
    assert any("preamble" in record.message.lower() for record in caplog.records)


def test_the_preamble_guard_leaves_a_plain_grounded_answer_alone(monkeypatch):
    """The guard must not become a way to reject every answer that mentions a passage."""
    fake = FakeOllama(
        models=("stub:1b",),
        response="Reading therapy $7.60 is the activity that involves reading.\nEVIDENCE: 1",
    )
    fake.install(monkeypatch)
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "stub:1b")
    result = llm_service.generate_answer(
        "Which activity involves reading?",
        ["[1] document=clinic_receipt.pdf page=1\nReading therapy $7.60"],
    )
    assert result is not None
    assert result.text.startswith("Reading therapy $7.60")


def test_a_scratchpad_never_reaches_the_chat_answer(monkeypatch, evidence_doc):
    fake = FakeOllama(response=SCRATCHPAD)
    fake.install(monkeypatch)
    db = SessionLocal()
    try:
        response = answer_question(
            db, ChatQueryRequest(question="Which activity involves reading?")
        )
    finally:
        db.close()
    assert response.engine != "llm_ollama"
    lowered = response.answer.lower()
    assert "evidence passage" not in lowered
    assert "we are given" not in lowered
    assert "i need to" not in lowered
    assert "passage [" not in lowered


@pytest.mark.skipif(
    not llm_service.provider_status(force=True)["reachable"],
    reason="local Ollama is not running",
)
def test_real_installed_model_answers_from_real_evidence(evidence_doc):
    """End-to-end against the installed model: no transport is mocked here.

    The assertion is that the model's own grounded words reach the user, not that
    the extractive fallback quietly answered instead. A warm llama3.2:3b takes a
    few seconds on this passage.
    """
    if not llm_service.is_usable():
        pytest.skip(f"configured model {llm_service.configured_model()!r} is not installed")
    db = SessionLocal()
    try:
        response = answer_question(
            db, ChatQueryRequest(question="Which activity involves reading?")
        )
    finally:
        db.close()
    assert response.engine == "llm_ollama", response.answer
    assert response.has_evidence is True
    # The model chooses how much to quote; what must hold is that what it says
    # comes from the cited passage.
    assert "reading" in response.answer.lower()
    assert response.sources[0].document_id == evidence_doc.id

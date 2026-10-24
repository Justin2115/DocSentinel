"""Evidence boundaries: a clause wrapped over two OCR lines must stay one passage.

OCR hands the pipeline one text box per detected line, so a sentence that the
typesetter wrapped across lines arrives as two lines with no punctuation between
them. Everything downstream - sliding windows, ranking, deduplication and the
deterministic sentence extractor - then has to agree on where the clause ends.
When it decides too early, the model is shown "… in accordance with the laws" and
the answer it was never given ("of the Republic of Indonesia") is scored as a
hallucination. These tests use invented documents only: the rule being pinned is
about line and sentence structure, not about any one file.
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.services.evidence_service import (
    EvidenceUnit,
    build_page_units,
    build_query_plan,
    dedupe_units,
    rank_units,
)
from app.services.rag_service import _extract_sentences

WRAPPED_PAGE = (
    "SERVICE AGREEMENT\n"
    "This agreement is governed by the law\n"
    "of the Republic of Genovia.\n"
    "Either party may end it with thirty days written notice.\n"
)
TAIL = "of the Republic of Genovia."
HEAD = "This agreement is governed by the law"


def _clause_units():
    units = build_page_units(1, "agreement_sample.pdf", 1, WRAPPED_PAGE)
    plan = build_query_plan("Whose law governs this agreement?")
    return rank_units(units, plan, specific={"law"}, limit=8), plan


def test_ranked_evidence_carries_the_whole_wrapped_clause():
    ranked, _ = _clause_units()
    complete = [
        unit for unit in ranked if HEAD in unit.text and TAIL in unit.text
    ]
    assert complete, (
        "no ranked window joins the wrapped clause; the model can only ever be "
        f"shown a fragment: {[u.text for u in ranked][:4]}"
    )


def test_the_complete_clause_is_ranked_above_the_fragment():
    """The passage the answer is built from must not be the truncated one.

    Ranking only reaches the model through the evidence budget, so a complete
    clause that ranks below its own fragment is still the evidence the model gets
    - which is what the fragment used to be.
    """
    ranked, _ = _clause_units()
    assert ranked[0].text == f"{HEAD}\n{TAIL}", [unit.text for unit in ranked]


def test_dedupe_keeps_the_window_that_finishes_the_sentence():
    """Subsumption may not spend a complete clause to keep its own head."""
    head = EvidenceUnit(
        document_id=1,
        document_name="agreement_sample.pdf",
        page_number=1,
        chunk_index=0,
        text="Payment shall be made in accordance with the laws",
        lines=("Payment shall be made in accordance with the laws",),
        source_kind="line_window",
        matched_terms=("law",),
    )
    whole = EvidenceUnit(
        document_id=1,
        document_name="agreement_sample.pdf",
        page_number=1,
        chunk_index=0,
        text="Payment shall be made in accordance with the laws\nof the Republic of Genovia.",
        lines=(
            "Payment shall be made in accordance with the laws",
            TAIL,
        ),
        source_kind="line_window",
        matched_terms=("law",),
    )
    kept = dedupe_units([head, whole])
    assert any(TAIL in unit.text for unit in kept), (
        "the complete window was discarded as a duplicate of its own first line"
    )


def test_dedupe_still_prefers_the_tighter_window_between_two_complete_units():
    """The fix is about unfinished sentences, not about making windows longer.

    A heading and its full sentence are both finished, so the overlap is still
    the same evidence twice and the shorter window is the one worth a slot.
    """
    heading = EvidenceUnit(
        document_id=1,
        document_name="agreement_sample.pdf",
        page_number=1,
        chunk_index=0,
        text="Governing law applies.",
        lines=("Governing law applies.",),
        source_kind="line_window",
        matched_terms=("law",),
    )
    span = EvidenceUnit(
        document_id=1,
        document_name="agreement_sample.pdf",
        page_number=1,
        chunk_index=0,
        text="Governing law applies.\nTermination requires notice.",
        lines=("Governing law applies.", "Termination requires notice."),
        source_kind="line_window",
        matched_terms=("law",),
    )
    assert [unit.text for unit in dedupe_units([heading, span])] == [
        "Governing law applies."
    ]


def test_extract_sentences_keeps_a_wrapped_clause_as_one_sentence():
    """Same defect on the deterministic path: sentences are cut before they end."""
    sentences = _extract_sentences(WRAPPED_PAGE)
    assert any(HEAD in sentence and TAIL in sentence for sentence in sentences), sentences


def test_extract_sentences_does_not_run_two_distinct_sentences_together():
    """Joining wrapped lines must not swallow a sentence that already ended."""
    sentences = _extract_sentences(
        "The premium is Rs 500.00 per year.\nRenewal is automatic unless cancelled.\n"
    )
    assert sentences[0] == "The premium is Rs 500.00 per year."


class _RecordingOllama:
    """Stands in for the runtime and keeps the prompt it was sent."""

    def __init__(self, model: str):
        self.model = model
        self.prompts: list[str] = []

    def get(self, path, timeout):
        return {"models": [{"name": self.model}]}

    def post(self, path, payload, seconds):
        self.prompts.append(payload["prompt"])
        return {
            "response": "The law of the Republic of Genovia governs it.\nEVIDENCE: 1",
            "model": self.model,
            "eval_count": 12,
        }


def test_the_model_is_asked_with_the_clause_intact(monkeypatch):
    """What the retrieval layer hands the model is the whole defect, end to end.

    The unit above proves the window exists; this one proves it survives the
    evidence budget, the citation limit and the prompt builder, which is where a
    truncated fragment used to reach the model and its answer was then scored as a
    fabrication.
    """
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.models.document import Document, DocumentPage, OCRResult
    from app.schemas.query import ChatQueryRequest
    from app.services import llm_service
    from app.services.rag_service import answer_question

    stub = _RecordingOllama("stub-for-boundaries:1b")
    monkeypatch.setattr(llm_service, "_get", stub.get)
    monkeypatch.setattr(llm_service, "_post", stub.post)
    monkeypatch.setattr(llm_service, "_status_cache", {"checked_at": 0.0, "payload": None})
    monkeypatch.setattr(settings, "OLLAMA_MODEL", stub.model)
    monkeypatch.setattr(settings, "OLLAMA_ENABLED", True)

    db = SessionLocal()
    document = Document(
        original_filename="boundary_sample.pdf",
        stored_filename="boundary_sample.pdf",
        file_path="uploads/boundary_sample.pdf",
        file_type="application/pdf",
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
            extracted_text=WRAPPED_PAGE,
        )
    )
    db.commit()
    try:
        response = answer_question(
            db, ChatQueryRequest(question="Whose law governs this agreement?")
        )
    finally:
        db.query(OCRResult).filter(OCRResult.document_id == document.id).delete()
        db.query(DocumentPage).filter(DocumentPage.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.commit()
        db.close()
        llm_service._status_cache["payload"] = None

    assert stub.prompts, "the model was never asked"
    assert f"{HEAD}\n{TAIL}" in stub.prompts[0], stub.prompts[0][:400]
    assert response.engine == "llm_ollama"

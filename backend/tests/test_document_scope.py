"""Document-scope enforcement for the chatbot (spec Priority E).

The "+" selector sends ``document_ids``; the server must apply that scope before
retrieval and evidence assembly, so an unselected document cannot win on a
stronger match, and an empty list must keep meaning "the whole library".
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult
from app.schemas.query import ChatQueryRequest
from app.services.embedding_service import (
    delete_document_embeddings,
    index_document_embeddings,
    set_encode_override,
)
from app.services.rag_service import answer_question
from tests.helpers import get_test_auth_headers, mock_multilingual_encode


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    """Deterministic, offline answers: the extractive path is what is scoped."""
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "is_usable", lambda: False)


@pytest.fixture(autouse=True)
def mock_encoder():
    set_encode_override(mock_multilingual_encode)
    yield
    set_encode_override(None)


def _make_document(filename: str, text: str, department: str) -> int:
    db = SessionLocal()
    try:
        doc = Document(
            original_filename=filename,
            stored_filename=filename,
            file_path=f"uploads/{filename}",
            file_type="application/pdf",
            file_size=2048,
            status="indexed",
            department=department,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        page = DocumentPage(document_id=doc.id, page_number=1)
        db.add(page)
        db.commit()
        db.refresh(page)
        db.add(
            OCRResult(
                document_id=doc.id,
                page_id=page.id,
                extracted_text=text,
                ocr_engine="PaddleOCR",
            )
        )
        db.commit()
        index_document_embeddings(db, doc.id)
        return doc.id
    finally:
        db.close()


@pytest.fixture
def scoped_documents():
    """Two documents whose wording is identical apart from the amount.

    The stronger-sounding invoice is deliberately the one a scoped question must
    never reach.
    """
    selected = _make_document(
        "clinic_invoice.pdf",
        "Invoice Number\nCLIN-11\n"
        "Due Date\nFebruary 2, 2019\n"
        "Total Due\n$12.00\n"
        "BATH TIME session charge.\n",
        "Clinic",
    )
    excluded = _make_document(
        "vendor_invoice.pdf",
        "Invoice Number\nVEND-77\n"
        "Due Date\nMarch 3, 2019\n"
        "Total Due\n$93.50\n"
        "Late payment is subject to fees of 5% per month.\n",
        "Finance",
    )
    ids = {"selected": selected, "excluded": excluded}
    yield ids
    db = SessionLocal()
    try:
        for doc_id in ids.values():
            delete_document_embeddings(db, doc_id)
            db.query(OCRResult).filter(OCRResult.document_id == doc_id).delete()
            db.query(DocumentPage).filter(DocumentPage.document_id == doc_id).delete()
            db.query(Document).filter(Document.id == doc_id).delete()
        db.commit()
    finally:
        db.close()


def _ask(question: str, document_ids=None, history=None):
    db = SessionLocal()
    try:
        return answer_question(
            db,
            ChatQueryRequest(
                question=question, document_ids=document_ids, history=history or []
            ),
        )
    finally:
        db.close()


def test_unscoped_question_reaches_every_document(scoped_documents):
    response = _ask("What is the total due amount?")
    assert response.has_evidence
    assert "$93.50" in response.answer
    assert {source.document_id for source in response.sources} <= set(
        scoped_documents.values()
    )


def test_scope_excludes_a_strongly_matching_document(scoped_documents):
    scope = [scoped_documents["selected"]]
    response = _ask("What is the total due amount?", document_ids=scope)
    assert response.has_evidence
    assert "$12.00" in response.answer
    assert "$93.50" not in response.answer
    assert {source.document_id for source in response.sources} == set(scope)


def test_other_scope_excludes_the_first_document(scoped_documents):
    scope = [scoped_documents["excluded"]]
    response = _ask("What is the total due amount?", document_ids=scope)
    assert response.has_evidence
    assert "$93.50" in response.answer
    assert "$12.00" not in response.answer
    assert {source.document_id for source in response.sources} == set(scope)


def test_empty_scope_list_means_the_whole_library(scoped_documents):
    response = _ask("What is the total due amount?", document_ids=[])
    assert response.has_evidence
    assert "$93.50" in response.answer


def test_unknown_scope_id_is_rejected_without_retrieval(scoped_documents):
    response = _ask("What is the total due amount?", document_ids=[9_999_999])
    assert response.engine == "unauthorized"
    assert response.has_evidence is False
    assert response.sources == []


def test_scope_survives_a_follow_up_with_history(scoped_documents):
    scope = [scoped_documents["selected"]]
    history = [
        {"role": "user", "content": "What is the total due amount?"},
        {"role": "assistant", "content": "The total due is $12.00."},
    ]
    response = _ask(
        "Which invoice number does that refer to?",
        document_ids=scope,
        history=history,
    )
    assert {source.document_id for source in response.sources} <= set(scope)
    if response.has_evidence:
        assert "VEND-77" not in response.answer


def test_api_accepts_document_ids(client_admin_headers, scoped_documents):
    client = TestClient(app)
    scope = [scoped_documents["excluded"]]
    response = client.post(
        "/api/query/ask",
        json={"question": "What is the total due amount?", "document_ids": scope},
        headers=client_admin_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["question"] == "What is the total due amount?"
    assert "$93.50" in payload["answer"]
    assert {source["document_id"] for source in payload["sources"]} == set(scope)


@pytest.fixture
def client_admin_headers():
    db = SessionLocal()
    try:
        return get_test_auth_headers(db)
    finally:
        db.close()


@pytest.fixture
def damaged_receipt():
    """One page whose total label the OCR truncated, surrounded by other amounts."""
    doc_id = _make_document(
        "clinic_walk_receipt.png",
        "SLLF\nCAR\nEARLY\nWALK\n$12.10\nLONC\nSHOWER\n$3450\n"
        "A\nBOOK\n$7.60\nTOTA\n$250.0\nNON-REFONDABLE\n",
        "Clinic",
    )
    try:
        yield doc_id
    finally:
        db = SessionLocal()
        try:
            delete_document_embeddings(db, doc_id)
            db.query(OCRResult).filter(OCRResult.document_id == doc_id).delete()
            db.query(DocumentPage).filter(DocumentPage.document_id == doc_id).delete()
            db.query(Document).filter(Document.id == doc_id).delete()
            db.commit()
        finally:
            db.close()


def test_scope_keeps_the_labelled_value_above_page_noise(damaged_receipt):
    """Inside a chosen scope, a damaged label still beats an unmatched figure.

    Every entry on the page is equally admissible on similarity, so if the
    in-scope preference outweighed contact with the question's wording, all the
    amounts tied and the answer abstained instead of reading the labelled total.
    """
    response = _ask(
        "What is the total amount printed on the receipt?",
        document_ids=[damaged_receipt],
    )
    assert response.has_evidence, response.answer
    assert "$250.0" in response.answer
    assert response.sources[0].document_id == damaged_receipt


@pytest.fixture
def numbered_files():
    """Two documents whose names are only reachable through the spaced spelling."""
    ids = {
        "File_04_pages_016-020.pdf": _make_document(
            "File_04_pages_016-020.pdf",
            "Water charges\n$5.00\nMeter 214\n",
            "Works",
        ),
        "File_05_pages_021-025.pdf": _make_document(
            "File_05_pages_021-025.pdf",
            "Sanction order SP-Fund-2018-19\n$5.00\n",
            "Works",
        ),
    }
    try:
        yield ids
    finally:
        db = SessionLocal()
        try:
            for doc_id in ids.values():
                delete_document_embeddings(db, doc_id)
                db.query(OCRResult).filter(OCRResult.document_id == doc_id).delete()
                db.query(DocumentPage).filter(DocumentPage.document_id == doc_id).delete()
                db.query(Document).filter(Document.id == doc_id).delete()
            db.commit()
        finally:
            db.close()


def test_spaced_file_reference_anchors_both_files(numbered_files):
    """The name "File 04" is as much a reference to the file as "file_04" is."""
    from app.services.evidence_service import resolve_document_scope

    db = SessionLocal()
    try:
        anchored = resolve_document_scope(
            db, "Compare the main purpose of File 04 and File 05."
        )
    finally:
        db.close()

    assert set(numbered_files.values()) <= set(anchored), (
        "a spaced filename reference must still select the documents it names"
    )


# --- the catalog shortcut, which reads the document table instead of the pool ---


def test_catalog_question_unscoped_names_every_document(scoped_documents):
    response = _ask("List the documents available in the system.")
    assert response.engine == "system_metadata"
    assert "clinic_invoice.pdf" in response.answer
    assert "vendor_invoice.pdf" in response.answer


def test_catalog_question_stays_inside_the_selected_document(scoped_documents):
    """A selected document narrows the catalog answer, not just the retrieval.

    This was the reported leak: with only one file attached, the listing shortcut
    ran before the scope existed and named a file the user had not selected.
    """
    scope = [scoped_documents["selected"]]
    response = _ask("List the documents available in the system.", document_ids=scope)
    assert response.engine == "system_metadata"
    assert "clinic_invoice.pdf" in response.answer
    assert "vendor_invoice.pdf" not in response.answer


def test_catalog_question_with_the_other_scope(scoped_documents):
    scope = [scoped_documents["excluded"]]
    response = _ask("How many documents are in the system?", document_ids=scope)
    assert "1" in response.answer
    assert "clinic_invoice.pdf" not in response.answer


# --- follow-ups that ask what the selected document is about ---


def test_overview_follow_up_is_answered_from_the_selected_document(scoped_documents):
    scope = [scoped_documents["selected"]]
    response = _ask("What is it about?", document_ids=scope)
    assert response.has_evidence, response.answer
    assert "clinic_invoice.pdf" in response.answer
    assert "vendor_invoice" not in response.answer
    assert {source.document_id for source in response.sources} == set(scope)


def test_overview_quotation_leads_with_the_front_of_the_scope(scoped_documents):
    scope = [scoped_documents["excluded"]]
    response = _ask("What is the main subject of this document?", document_ids=scope)
    assert response.has_evidence, response.answer
    assert "VEND-77" in response.answer
    assert "CLIN-11" not in response.answer
    assert response.sources[0].source_kind == "front_matter"
    assert {source.document_id for source in response.sources} == set(scope)


def test_overview_without_a_selected_document_quotes_no_front_matter(
    scoped_documents,
):
    """A vague question that names no document gets no document's opening lines.

    Front matter is only ever read for the ids the user selected (or a question
    that named them), so an unscoped "what is it about?" cannot pick an arbitrary
    file and present its header as the answer.
    """
    response = _ask("What is it about?")
    assert all(source.source_kind != "front_matter" for source in response.sources)


def test_front_matter_units_read_only_the_ids_they_are_given(scoped_documents):
    from app.services.evidence_service import front_matter_units

    db = SessionLocal()
    try:
        units = front_matter_units(db, [scoped_documents["excluded"], 9_999_999])
    finally:
        db.close()

    assert [unit.document_id for unit in units] == [scoped_documents["excluded"]]
    assert units[0].source_kind == "front_matter"
    assert "Late payment" in units[0].text


# --- similarity reporting ---


def test_unscored_evidence_reports_no_similarity(scoped_documents, monkeypatch):
    """A passage that never went through the vector store has no score to show.

    It used to be cited as "Similarity: 0.00", which reads as a measurement saying
    the passage is unrelated rather than as the absence of one.
    """
    from app.schemas.search import SemanticSearchResponse
    from app.services import embedding_service

    monkeypatch.setattr(
        embedding_service,
        "semantic_search",
        lambda *a, **k: SemanticSearchResponse(items=[], total=0, query="q"),
    )
    scope = [scoped_documents["selected"]]
    response = _ask("What is the total due amount?", document_ids=scope)
    assert response.has_evidence, response.answer
    assert "$12.00" in response.answer
    assert response.sources[0].similarity is None
    assert "lexical" in response.sources[0].support


def test_scored_evidence_keeps_its_similarity(scoped_documents):
    scope = [scoped_documents["selected"]]
    response = _ask("What is the total due amount?", document_ids=scope)
    assert response.sources[0].similarity is not None
    assert response.sources[0].similarity > 0.0


def test_scope_drops_any_citation_outside_the_selection(scoped_documents):
    from app.schemas.query import ChatQueryResponse, SourceCitation
    from app.services.rag_service import _enforce_scope

    allowed = [scoped_documents["selected"]]
    response = ChatQueryResponse(
        answer="test",
        sources=[
            SourceCitation(
                document_id=allowed[0],
                document_name="clinic_invoice.pdf",
                page_number=1,
                snippet="Total Due $12.00",
                similarity=0.4,
            ),
            SourceCitation(
                document_id=scoped_documents["excluded"],
                document_name="vendor_invoice.pdf",
                page_number=1,
                snippet="Total Due $93.50",
                similarity=0.9,
            ),
        ],
        confidence=0.4,
        question="test",
    )
    kept = _enforce_scope(response, allowed)
    assert [source.document_id for source in kept.sources] == allowed
    assert _enforce_scope(kept, allowed) is kept


# --- the local model may not name what the evidence does not contain ---


def test_names_supported_by_evidence_blocks_an_unsupported_name(scoped_documents):
    from app.services.evidence_service import front_matter_units
    from app.services.rag_service import _names_supported_by_evidence

    db = SessionLocal()
    try:
        units = front_matter_units(db, [scoped_documents["selected"]])
    finally:
        db.close()

    assert _names_supported_by_evidence(
        "CLIN-11 is due on February 2, 2019.", units, "What is the due date?"
    )
    assert _names_supported_by_evidence(
        "The BATH TIME session charge is listed.", units, "What is the total due?"
    )
    # A file the user never selected, and a person no passage mentions.
    assert not _names_supported_by_evidence(
        "See vendor_invoice.pdf for the charge.", units, "What is the total due?"
    )
    assert not _names_supported_by_evidence(
        "Rajesh Kumar owes this amount.", units, "What is the total due?"
    )

"""Aggregate intent and cross-document evidence budget (spec Priority A/B).

A plural noun can be the *kind of value* a question asks for ("what are the
electricity charges") rather than a request for the whole collection. When it is
mistaken for a collection cue, every document that happens to hold a number of
that kind joins the evidence pool and the matching document is pushed out. These
tests pin both halves: the interpretation, and the budget an aggregate question
spends across documents.
"""

import sys
from pathlib import Path

import pytest

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.db.session import SessionLocal
from app.models.document import Document, DocumentPage, OCRResult
from app.schemas.query import ChatQueryRequest
from app.services.embedding_service import (
    delete_document_embeddings,
    index_document_embeddings,
)
from app.services.evidence_service import (
    EvidenceUnit,
    QueryPlan,
    ValueToken,
    build_query_plan,
    rank_units,
)
from app.services.rag_service import answer_question


def test_value_noun_is_not_a_collection_cue():
    plan = build_query_plan("How much are the electricity charges?")
    assert plan.value_kind == "amount"
    assert plan.aggregate is False


def test_explicit_collection_frame_still_aggregates():
    for question in (
        "How much did I pay across all my bills?",
        "Give me every amount in each document",
        "Compare the totals of these invoices",
    ):
        assert build_query_plan(question).aggregate is True, question


def test_entry_noun_without_a_value_frame_collects():
    assert build_query_plan("What activities are listed?").aggregate is True


def test_yes_no_question_does_not_demand_a_value():
    """The line that answers a yes/no question usually carries no number.

    Forcing a value into the window made "is the amount non-refundable" read as an
    amount question that the wording itself could never satisfy.
    """
    plan = build_query_plan("Is the package amount non-refundable?")
    assert plan.yes_no is True
    assert plan.value_kind is None

    quoted = build_query_plan("Is the total $250.0?")
    assert quoted.yes_no is True
    assert quoted.value_kind == "amount"
    assert "$250.0" in quoted.numeric_literals or "250.0" in quoted.numeric_literals


def _amount_unit(document_id: int, raw: str, **kwargs) -> EvidenceUnit:
    text = kwargs.pop("text", f"Chit {document_id} {raw}")
    defaults = dict(
        document_name=f"doc_{document_id}.pdf",
        page_number=1,
        chunk_index=0,
        text=text,
        lines=(text,),
        source_kind="entry",
        values=(ValueToken(raw=raw, kind="amount", state="verified"),),
    )
    defaults.update(kwargs)
    return EvidenceUnit(document_id=document_id, **defaults)


def test_matching_document_keeps_its_slots_in_a_collection():
    """A true collection question may not lose its subject to page noise.

    The unrelated files are deliberately the lower document ids: ordering by
    document id alone used to fill the pool with them and truncate the one
    window that actually answers the question.
    """
    plan = build_query_plan("List every amount recorded across all documents")
    assert plan.aggregate is True

    distractors = [
        _amount_unit(document_id, f"${index}.00", coverage=0.0, strength=0.0)
        for document_id in range(1, 6)
        for index in range(4)
    ]
    gold = _amount_unit(
        99,
        "$42.00",
        text="Electricity Charges Rs 42.00",
        lines=("Electricity Charges Rs 42.00",),
        coverage=1.0,
        strength=1.0,
        matched_terms=("electricity", "charges"),
        specific_matches=("electricity",),
    )

    ranked = rank_units([*distractors, gold], plan, specific={"electricity"}, limit=5)
    assert any(unit.document_id == 99 for unit in ranked), (
        "the matching document was truncated out of the aggregate pool"
    )
    assert ranked[0].document_id == 99, (
        "a collection answer must lead with the document that answers it"
    )


def test_collection_question_still_spreads_over_documents():
    plan = build_query_plan("List every amount recorded across all documents")
    units = [
        _amount_unit(document_id, f"${document_id}.00")
        for document_id in range(1, 8)
    ]
    ranked = rank_units(units, plan, specific=set(), limit=12)
    assert len({unit.document_id for unit in ranked}) == len(units)


def test_ranked_reading_order_survives_the_budget():
    plan = build_query_plan("List every amount recorded across all documents")
    units = [
        _amount_unit(3, "$3.00", page_number=2, chunk_index=1, line_index=5),
        _amount_unit(3, "$3.10", page_number=1, chunk_index=0, line_index=2),
        _amount_unit(1, "$1.00", page_number=1, chunk_index=0, line_index=9),
    ]
    ranked = rank_units(units, plan, specific=set(), limit=12)
    assert [(u.document_id, u.page_number) for u in ranked] == [(1, 1), (3, 1), (3, 2)]


@pytest.fixture
def utility_documents():
    """The matching invoice is the newest document in the library."""
    corpus = [
        ("clinic_receipt_apr.pdf", "Consultation Fee\n$12.00\nPaid on 04/04/2024\n"),
        ("activity_bill_4410.pdf", "Activity Charges\nHall booking\n$25.00\n"),
        ("utility_bill_may.pdf", "Meter Reading 214\nElectricity Charges\n$42.00\n"),
    ]
    db = SessionLocal()
    created: list[int] = []
    try:
        for filename, text in corpus:
            doc = Document(
                original_filename=filename,
                stored_filename=filename,
                file_path=f"uploads/{filename}",
                file_type="application/pdf",
                file_size=2048,
                status="indexed",
                department="Finance",
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
            created.append(doc.id)
        yield dict(zip([name for name, _ in corpus], created))
    finally:
        db.close()

    db = SessionLocal()
    try:
        for doc_id in created:
            delete_document_embeddings(db, doc_id)
            db.query(OCRResult).filter(OCRResult.document_id == doc_id).delete()
            db.query(DocumentPage).filter(DocumentPage.document_id == doc_id).delete()
            db.query(Document).filter(Document.id == doc_id).delete()
        db.commit()
    finally:
        db.close()


def test_amount_question_answers_from_the_matching_document(utility_documents):
    db = SessionLocal()
    try:
        response = answer_question(
            db,
            ChatQueryRequest(question="How much are the electricity charges?"),
        )
    finally:
        db.close()

    assert response.has_evidence
    assert "$42.00" in response.answer
    assert "$12.00" not in response.answer
    assert utility_documents["utility_bill_may.pdf"] in {
        source.document_id for source in response.sources
    }


def test_plan_defaults_keep_non_aggregate_questions_single_target():
    plan = QueryPlan(question="x")
    assert plan.aggregate is False


@pytest.fixture
def damaged_receipt():
    """One receipt page whose labels the OCR corrupted.

    The wording mirrors the observed damage: a truncated word ("TOTA") and a
    misspelled one ("REFONDABLE") that a fuzzy match has to reach without the
    question naming the file.
    """
    db = SessionLocal()
    doc = Document(
        original_filename="clinic_walk_receipt.png",
        stored_filename="clinic_walk_receipt.png",
        file_path="uploads/clinic_walk_receipt.png",
        file_type="image/png",
        file_size=4096,
        status="indexed",
        department="Clinic",
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
            extracted_text=(
                "SLLF\nCAR\nEARLY\nWALK\n$12.10\nMORNINC\nTOTA\n$250.0\n"
                "100/.\nNON-REFONDABLE\n"
            ),
            ocr_engine="PaddleOCR",
        )
    )
    db.commit()
    index_document_embeddings(db, doc.id)
    try:
        yield doc.id
    finally:
        delete_document_embeddings(db, doc.id)
        db.query(OCRResult).filter(OCRResult.document_id == doc.id).delete()
        db.query(DocumentPage).filter(DocumentPage.document_id == doc.id).delete()
        db.query(Document).filter(Document.id == doc.id).delete()
        db.commit()
        db.close()


def _ask(question: str, document_ids=None):
    db = SessionLocal()
    try:
        return answer_question(
            db, ChatQueryRequest(question=question, document_ids=document_ids)
        )
    finally:
        db.close()


def test_damaged_wording_is_reached_without_naming_the_file(damaged_receipt):
    response = _ask("Is the amount non-refundable?")
    assert response.has_evidence, response.answer
    assert "REFONDABLE" in response.answer.upper()
    assert damaged_receipt in {source.document_id for source in response.sources}


def test_selector_scope_lets_truncated_labels_through(damaged_receipt):
    """Relaxed matching is only admissible inside a scope the user chose."""
    scoped = _ask("How much is the morning walk?", document_ids=[damaged_receipt])
    assert scoped.has_evidence, scoped.answer
    assert "$12.10" in scoped.answer
    unscoped = _ask("How much is the morning walk?")
    assert "$12.10" in unscoped.answer or unscoped.has_evidence is False


def test_an_artifact_noun_does_not_outrank_the_value_noun():
    """A document-type noun must not decide which value the question wants.

    "invoice", "challan" and "बिल" name the artifact, so they used to win the
    most-specific-hint tie-break on length ("invoice" is longer than "total") and
    the flagship amount question answered with the invoice number instead.
    """
    assert build_query_plan("What is the total due amount on invoice INV-3337?").value_kind == "amount"
    assert build_query_plan("What is the total due on the invoice?").value_kind == "amount"
    assert build_query_plan("बिलची रक्कम किती?").value_kind == "amount"
    # A question that does ask for a designation still gets one.
    assert build_query_plan("What is the invoice number?").value_kind == "identifier"
    assert build_query_plan("What is the challan reference?").value_kind == "identifier"
    assert build_query_plan("चालान संख्या किती आहे?").value_kind == "identifier"
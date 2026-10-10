import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.rbac import UserRole
from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult
from app.models.embedding import EmbeddingIndex
from app.models.user import User
from app.services.embedding_service import (
    chunk_text,
    index_document_embeddings,
    normalize_multilingual_text,
    reindex_all_documents,
    reset_chroma_client,
    semantic_search,
    set_encode_override,
)
from app.services.search_service import hybrid_search, keyword_search
from tests.helpers import (
    get_test_auth_headers,
    use_test_embeddings,
)

client = TestClient(app)


def test_multilingual_chunking_preserves_indic_sentence_boundaries():
    text = (
        "This is the initial English intro statement.\n\n"
        "यह पहला हिंदी वाक्य है। यह दूसरा हिंदी वाक्य है और विवरण प्रस्तुत करता है। "
        "या बिलाचे पैसे अद्याप भरलेले नाहीत। कृपया त्वरित भरावे."
    )
    chunks = chunk_text(text, chunk_size=120, overlap=30)
    assert len(chunks) >= 2
    assert any("हिंदी वाक्य है।" in ch for ch in chunks)
    assert any("भरलेले नाहीत।" in ch for ch in chunks)


def test_multilingual_text_normalization():
    raw = "यह  चालान\r\nभुगतान\xa0बाकी है।"
    normalized = normalize_multilingual_text(raw)
    assert "\r" not in normalized
    assert "\xa0" not in normalized
    assert "यह  चालान\nभुगतान बाकी है।" == normalized


def test_cross_language_retrieval_english_to_hindi_and_marathi(tmp_path):
    use_test_embeddings(tmp_path, multilingual=True)
    db = SessionLocal()

    doc_hi = Document(
        original_filename="hindi_invoice.pdf",
        stored_filename="hindi_invoice.pdf",
        file_path="uploads/hindi_invoice.pdf",
        file_type="application/pdf",
        status="indexed",
    )
    doc_mr = Document(
        original_filename="marathi_bill.pdf",
        stored_filename="marathi_bill.pdf",
        file_path="uploads/marathi_bill.pdf",
        file_type="application/pdf",
        status="indexed",
    )
    doc_unrelated = Document(
        original_filename="sports_news.pdf",
        stored_filename="sports_news.pdf",
        file_path="uploads/sports_news.pdf",
        file_type="application/pdf",
        status="indexed",
    )

    try:
        db.add_all([doc_hi, doc_mr, doc_unrelated])
        db.commit()
        db.refresh(doc_hi)
        db.refresh(doc_mr)
        db.refresh(doc_unrelated)

        db.add(OCRResult(document_id=doc_hi.id, extracted_text="यह चालान अभी तक भुगतान नहीं हुआ है। बकाया राशि तुरंत जमा करें।"))
        db.add(OCRResult(document_id=doc_mr.id, extracted_text="या बिलाचे पैसे अद्याप भरलेले नाहीत। देयक त्वरित अदा करावे."))
        db.add(OCRResult(document_id=doc_unrelated.id, extracted_text="Football championship tournament finals match scheduled tomorrow."))
        db.commit()

        index_document_embeddings(db, doc_hi.id)
        index_document_embeddings(db, doc_mr.id)
        index_document_embeddings(db, doc_unrelated.id)

        en_query_result = semantic_search(db, "unpaid invoice payment", top_k=10, min_score=0.1)
        retrieved_ids = [hit.document_id for hit in en_query_result.items]

        assert doc_hi.id in retrieved_ids, "English query must retrieve semantically relevant Hindi document"
        assert doc_mr.id in retrieved_ids, "English query must retrieve semantically relevant Marathi document"

        hi_query_result = semantic_search(db, "चालान भुगतान", top_k=10, min_score=0.1)
        hi_retrieved_ids = [hit.document_id for hit in hi_query_result.items]
        assert doc_hi.id in hi_retrieved_ids
        assert doc_mr.id in hi_retrieved_ids

        mr_query_result = semantic_search(db, "बिलाचे पैसे", top_k=10, min_score=0.1)
        mr_retrieved_ids = [hit.document_id for hit in mr_query_result.items]
        assert doc_mr.id in mr_retrieved_ids
        assert doc_hi.id in mr_retrieved_ids

    finally:
        db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id.in_([doc_hi.id, doc_mr.id, doc_unrelated.id])).delete()
        db.query(OCRResult).filter(OCRResult.document_id.in_([doc_hi.id, doc_mr.id, doc_unrelated.id])).delete()
        db.query(Document).filter(Document.id.in_([doc_hi.id, doc_mr.id, doc_unrelated.id])).delete()
        db.commit()
        db.close()
        set_encode_override(None)
        reset_chroma_client()


def test_mixed_language_code_switched_retrieval(tmp_path):
    use_test_embeddings(tmp_path, multilingual=True)
    db = SessionLocal()

    doc_mixed = Document(
        original_filename="hinglish_doc.pdf",
        stored_filename="hinglish_doc.pdf",
        file_path="uploads/hinglish_doc.pdf",
        file_type="application/pdf",
        status="indexed",
    )
    try:
        db.add(doc_mixed)
        db.commit()
        db.refresh(doc_mixed)

        db.add(OCRResult(document_id=doc_mixed.id, extracted_text="Invoice cha payment pending ahe, please settle amount promptly."))
        db.commit()
        index_document_embeddings(db, doc_mixed.id)

        res_en = semantic_search(db, "pending invoice payment", top_k=5, min_score=0.1)
        assert any(hit.document_id == doc_mixed.id for hit in res_en.items)

        res_mr = semantic_search(db, "बिलाचे पैसे", top_k=5, min_score=0.1)
        assert any(hit.document_id == doc_mixed.id for hit in res_mr.items)

    finally:
        db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == doc_mixed.id).delete()
        db.query(OCRResult).filter(OCRResult.document_id == doc_mixed.id).delete()
        db.query(Document).filter(Document.id == doc_mixed.id).delete()
        db.commit()
        db.close()
        set_encode_override(None)
        reset_chroma_client()


def test_hybrid_ranking_semantic_priority_over_accidental_keyword(tmp_path):
    use_test_embeddings(tmp_path, multilingual=True)
    db = SessionLocal()

    doc_semantic = Document(
        original_filename="vendor_billing_statement.pdf",
        stored_filename="vendor_billing_statement.pdf",
        file_path="uploads/vendor_billing_statement.pdf",
        file_type="application/pdf",
        status="indexed",
    )
    doc_keyword = Document(
        original_filename="website_terms.pdf",
        stored_filename="website_terms.pdf",
        file_path="uploads/website_terms.pdf",
        file_type="application/pdf",
        status="indexed",
    )

    try:
        db.add_all([doc_semantic, doc_keyword])
        db.commit()
        db.refresh(doc_semantic)
        db.refresh(doc_keyword)

        db.add(OCRResult(
            document_id=doc_semantic.id,
            extracted_text="The unpaid vendor invoice is pending settlement before due date.",
        ))
        db.add(OCRResult(
            document_id=doc_keyword.id,
            extracted_text="By accessing this website, payment gateways are subject to general terms.",
        ))
        db.commit()

        index_document_embeddings(db, doc_semantic.id)
        index_document_embeddings(db, doc_keyword.id)

        hybrid_res = hybrid_search(db, "unpaid invoice bill", skip=0, limit=10, min_score=0.1)
        assert hybrid_res.total >= 1
        top_hit = hybrid_res.items[0]

        assert top_hit.document_id == doc_semantic.id
        assert top_hit.score > 0.6

    finally:
        db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id.in_([doc_semantic.id, doc_keyword.id])).delete()
        db.query(OCRResult).filter(OCRResult.document_id.in_([doc_semantic.id, doc_keyword.id])).delete()
        db.query(Document).filter(Document.id.in_([doc_semantic.id, doc_keyword.id])).delete()
        db.commit()
        db.close()
        set_encode_override(None)
        reset_chroma_client()


def test_rbac_permission_enforcement_in_semantic_and_hybrid_search(tmp_path):
    use_test_embeddings(tmp_path, multilingual=True)
    db = SessionLocal()

    admin_headers = get_test_auth_headers(db, email="admin_test@docsentinel.local", role=UserRole.ADMIN.value)
    hr_maker_headers = get_test_auth_headers(db, email="hr_maker@docsentinel.local", role=UserRole.UPLOAD_MAKER.value, department="HR")
    finance_maker_headers = get_test_auth_headers(db, email="fin_maker@docsentinel.local", role=UserRole.UPLOAD_MAKER.value, department="FINANCE")

    hr_user = db.query(User).filter(User.email == "hr_maker@docsentinel.local").first()
    fin_user = db.query(User).filter(User.email == "fin_maker@docsentinel.local").first()

    doc_hr = Document(
        original_filename="hr_employee_salary_policy.pdf",
        stored_filename="hr_employee_salary_policy.pdf",
        file_path="uploads/hr_employee_salary_policy.pdf",
        file_type="application/pdf",
        department="HR",
        uploaded_by=hr_user.id,
        status="indexed",
    )
    doc_finance = Document(
        original_filename="finance_tax_invoice.pdf",
        stored_filename="finance_tax_invoice.pdf",
        file_path="uploads/finance_tax_invoice.pdf",
        file_type="application/pdf",
        department="FINANCE",
        uploaded_by=fin_user.id,
        status="indexed",
    )

    try:
        db.add_all([doc_hr, doc_finance])
        db.commit()
        db.refresh(doc_hr)
        db.refresh(doc_finance)

        db.add(OCRResult(document_id=doc_hr.id, extracted_text="HR employee monthly payroll and salary compensation terms."))
        db.add(OCRResult(document_id=doc_finance.id, extracted_text="Finance tax invoice bill payment for quarterly audit."))
        db.commit()

        index_document_embeddings(db, doc_hr.id)
        index_document_embeddings(db, doc_finance.id)

        admin_search = client.get("/api/search", params={"q": "invoice payment"}, headers=admin_headers)
        assert admin_search.status_code == 200
        admin_doc_ids = [hit["document_id"] for hit in admin_search.json()["items"]]
        assert doc_finance.id in admin_doc_ids

        hr_search = client.get("/api/search", params={"q": "invoice payment"}, headers=hr_maker_headers)
        assert hr_search.status_code == 200
        hr_doc_ids = [hit["document_id"] for hit in hr_search.json()["items"]]
        assert doc_finance.id not in hr_doc_ids, "HR maker must NEVER see unauthorized Finance documents via search"

        hr_salary_search = client.get("/api/search/semantic", params={"q": "employee salary payroll"}, headers=hr_maker_headers)
        assert hr_salary_search.status_code == 200
        salary_doc_ids = [hit["document_id"] for hit in hr_salary_search.json()["items"]]
        assert doc_hr.id in salary_doc_ids

        fin_salary_search = client.get("/api/search/semantic", params={"q": "employee salary payroll"}, headers=finance_maker_headers)
        assert fin_salary_search.status_code == 200
        fin_salary_ids = [hit["document_id"] for hit in fin_salary_search.json()["items"]]
        assert doc_hr.id not in fin_salary_ids, "Finance maker must NEVER see unauthorized HR documents"

    finally:
        db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id.in_([doc_hr.id, doc_finance.id])).delete()
        db.query(OCRResult).filter(OCRResult.document_id.in_([doc_hr.id, doc_finance.id])).delete()
        db.query(Document).filter(Document.id.in_([doc_hr.id, doc_finance.id])).delete()
        db.commit()
        db.close()
        set_encode_override(None)
        reset_chroma_client()


def test_empty_query_and_missing_embeddings_handled_safely(tmp_path):
    use_test_embeddings(tmp_path, multilingual=True)
    db = SessionLocal()
    headers = get_test_auth_headers(db)

    res_empty = client.get("/api/search", params={"q": "   "}, headers=headers)
    assert res_empty.status_code == 200
    assert res_empty.json()["items"] == []

    res_sem_empty = client.get("/api/search/semantic", params={"q": "   "}, headers=headers)
    assert res_sem_empty.status_code == 200
    assert res_sem_empty.json()["items"] == []

    res_no_docs = semantic_search(db, "some query when index empty")
    assert res_no_docs.items == []
    assert res_no_docs.total == 0

    set_encode_override(None)
    reset_chroma_client()


def test_safe_reindexing_path(tmp_path):
    use_test_embeddings(tmp_path, multilingual=True)
    db = SessionLocal()
    admin_headers = get_test_auth_headers(db)

    doc = Document(
        original_filename="reindex_test.pdf",
        stored_filename="reindex_test.pdf",
        file_path="uploads/reindex_test.pdf",
        file_type="application/pdf",
        status="completed",
    )
    try:
        db.add(doc)
        db.commit()
        db.refresh(doc)

        db.add(OCRResult(document_id=doc.id, extracted_text="Invoice bill payment confirmation."))
        db.commit()

        count = reindex_all_documents(db, force=True)
        assert count >= 1

        api_reindex = client.post("/api/search/reindex", params={"force": "true"}, headers=admin_headers)
        assert api_reindex.status_code == 200
        assert api_reindex.json()["status"] == "success"
        assert api_reindex.json()["reindexed_documents"] >= 1

    finally:
        db.query(EmbeddingIndex).filter(EmbeddingIndex.document_id == doc.id).delete()
        db.query(OCRResult).filter(OCRResult.document_id == doc.id).delete()
        db.query(Document).filter(Document.id == doc.id).delete()
        db.commit()
        db.close()
        set_encode_override(None)
        reset_chroma_client()

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.search import SearchHit, SearchResponse, SemanticSearchResponse
from app.services.embedding_service import reindex_all_documents, semantic_search
from app.services.search_service import hybrid_search, keyword_search

router = APIRouter(
    prefix="/api/search",
    tags=["Search"],
)


@router.get(
    "",
    response_model=SearchResponse,
)
def search_documents(
    q: str = Query(..., min_length=1),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    mode: str = Query(
        "hybrid",
        description="hybrid (keyword + semantic), keyword, or semantic",
    ),
    min_score: float = Query(0.2, ge=0.0, le=1.0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search documents with keyword matching plus semantic retrieval.

    Enforces RBAC and department-level permissions on returned results.
    Default mode is hybrid: balanced semantic-first retrieval with complementary keyword hits.
    """
    clean_q = (q or "").strip()
    if not clean_q:
        return SearchResponse(items=[], total=0, skip=skip, limit=limit)

    normalized = (mode or "hybrid").strip().lower()
    if normalized == "keyword":
        return keyword_search(
            db, clean_q, skip=skip, limit=limit, user=current_user, aggregate_by_document=True
        )
    if normalized == "semantic":
        try:
            semantic_result = semantic_search(
                db,
                clean_q,
                top_k=limit,
                min_score=min_score,
                user=current_user,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=f"Semantic search failed: {error}",
            ) from error
        items = [
            SearchHit(
                document_id=hit.document_id,
                document_name=hit.document_name,
                page_number=hit.page_number,
                snippet=hit.snippet,
                match_field="semantic",
                score=hit.similarity,
                highlight_terms=hit.highlight_terms,
            )
            for hit in semantic_result.items
        ]
        return SearchResponse(
            items=items[skip : skip + limit],
            total=len(items),
            skip=skip,
            limit=limit,
        )
    return hybrid_search(
        db,
        clean_q,
        skip=skip,
        limit=limit,
        min_score=min_score,
        user=current_user,
    )


@router.get(
    "/semantic",
    response_model=SemanticSearchResponse,
)
def search_documents_semantic(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    min_score: float = Query(0.2, ge=0.0, le=1.0),
    aggregate_by_document: bool = Query(True, description="Aggregate results to 1 hit per document"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Semantic search across document chunks with RBAC permission enforcement."""
    clean_q = (q or "").strip()
    if not clean_q:
        return SemanticSearchResponse(items=[], total=0, query="")

    try:
        return semantic_search(
            db,
            clean_q,
            top_k=top_k,
            min_score=min_score,
            user=current_user,
            aggregate_by_document=aggregate_by_document,
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Semantic search failed: {error}",
        ) from error


@router.post(
    "/reindex",
    summary="Reindex embeddings across all documents with the active model (Admin only)",
)
def reindex_all_search_embeddings(
    force: bool = Query(False, description="Purge and re-index existing embeddings"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        count = reindex_all_documents(db, force=force)
        return {"status": "success", "reindexed_documents": count}
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Reindexing failed: {error}",
        ) from error


@router.post(
    "/ask",
    summary="Ask a question grounded in authorized documents (alias for /api/query/ask)",
)
def search_ask_alias(
    request: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.schemas.query import ChatQueryRequest
    from app.services.rag_service import answer_question

    chat_req = ChatQueryRequest(**request)
    return answer_question(db, chat_req, user=current_user)

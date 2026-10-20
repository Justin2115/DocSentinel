from fastapi import APIRouter, Depends, HTTPException, Query
import logging
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.search import AnswerRequest, AnswerResponse, SearchHit, SearchResponse, SemanticSearchResponse
from app.services.answer_service import answer_question
from app.services.embedding_service import semantic_search
from app.services.search_service import hybrid_search, keyword_search
from app.services.access_scope import authorized_document_ids

router = APIRouter(
    prefix="/api/search",
    tags=["Search"],
)
logger = logging.getLogger(__name__)


@router.post("/ask", response_model=AnswerResponse)
def ask_documents(
    request: AnswerRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Answer from the current user's retrieved documents and return verified source objects."""
    try:
        return answer_question(
            db,
            current_user,
            request.question,
            include_citations=request.include_citations,
            top_k=request.top_k,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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

    Default mode is hybrid: exact/ILIKE hits first, then semantically
    related chunks the keyword query would miss.
    """
    normalized = (mode or "hybrid").strip().lower()
    allowed_ids = authorized_document_ids(db, current_user)
    if normalized == "keyword":
        return keyword_search(db, q, skip=skip, limit=limit, authorized_ids=allowed_ids)
    if normalized == "semantic":
        try:
            semantic_result = semantic_search(
                db,
                q,
                top_k=limit,
                min_score=min_score,
                authorized_ids=allowed_ids,
            )
        except Exception as error:
            logger.exception("Semantic search failed")
            raise HTTPException(
                status_code=500,
                detail="Semantic search is temporarily unavailable",
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
            items=items[skip: skip + limit],
            total=len(items),
            skip=skip,
            limit=limit,
        )
    return hybrid_search(
        db,
        q,
        skip=skip,
        limit=limit,
        min_score=min_score,
        authorized_ids=allowed_ids,
    )


@router.get(
    "/semantic",
    response_model=SemanticSearchResponse,
)
def search_documents_semantic(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    min_score: float = Query(0.2, ge=0.0, le=1.0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        allowed_ids = authorized_document_ids(db, current_user)
        return semantic_search(db, q, top_k=top_k, min_score=min_score, authorized_ids=allowed_ids)
    except Exception as error:
        logger.exception("Semantic search failed")
        raise HTTPException(
            status_code=500,
            detail="Semantic search is temporarily unavailable",
        ) from error

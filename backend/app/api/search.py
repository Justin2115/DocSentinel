from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.search import SearchHit, SearchResponse, SemanticSearchResponse
from app.services.embedding_service import semantic_search
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
    db: Session = Depends(get_db),
):
    """Search documents with keyword matching plus semantic retrieval.

    Default mode is hybrid: exact/ILIKE hits first, then semantically
    related chunks the keyword query would miss.
    """
    normalized = (mode or "hybrid").strip().lower()
    if normalized == "keyword":
        return keyword_search(db, q, skip=skip, limit=limit)
    if normalized == "semantic":
        try:
            semantic_result = semantic_search(
                db,
                q,
                top_k=limit,
                min_score=min_score,
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
    )


@router.get(
    "/semantic",
    response_model=SemanticSearchResponse,
)
def search_documents_semantic(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    min_score: float = Query(0.2, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    try:
        return semantic_search(db, q, top_k=top_k, min_score=min_score)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Semantic search failed: {error}",
        ) from error

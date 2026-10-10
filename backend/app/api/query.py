"""RAG Chatbot query routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.query import ChatQueryRequest, ChatQueryResponse
from app.services.rag_service import answer_question

router = APIRouter(
    prefix="/api/query",
    tags=["Query & RAG"],
)


@router.post(
    "/ask",
    response_model=ChatQueryResponse,
    summary="Ask a natural-language question grounded in authorized documents",
)
def ask_question_endpoint(
    request: ChatQueryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return answer_question(db, request, user=current_user)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Question answering failed: {error}",
        ) from error


"""Account-scoped durable chat history and streaming generation routes."""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.conversation import Conversation, ConversationMessage
from app.models.user import User
from app.schemas.conversation import (
    ConversationCreate, ConversationDetail, ConversationMessageCreate,
    ConversationMessageOut, ConversationRename, ConversationSummary,
)
from app.schemas.query import ChatQueryRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["Conversations"])
_active_lock = threading.Lock()
_active_conversations: set[str] = set()


@router.get("/provider/status")
def provider_health(user: User = Depends(get_current_user)):
    """Expose non-secret local inference availability to authenticated users."""
    from app.services.llm_service import provider_status

    status = provider_status()
    return {
        "enabled": status["enabled"],
        "reachable": status["reachable"],
        "model_installed": status["model_installed"],
        "model": status["model"],
        "available_models": status["available_models"],
        "reason": status["reason"],
    }


def _owned(db: Session, conversation_id: str, user: User) -> Conversation:
    try:
        normalized = str(UUID(conversation_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Conversation not found") from None
    row = db.query(Conversation).filter(
        Conversation.id == normalized,
        Conversation.owner_user_id == user.id,
        Conversation.archived_at.is_(None),
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return row


def _summary(row: Conversation) -> ConversationSummary:
    return ConversationSummary.model_validate(row, from_attributes=True)


@router.post("", response_model=ConversationSummary, status_code=201)
def create_conversation(
    request: ConversationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    title = re.sub(r"\s+", " ", (request.title or "New conversation")).strip()[:120]
    row = Conversation(owner_user_id=user.id, title=title or "New conversation", chat_mode=request.mode)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _summary(row)


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    limit: int = Query(50, ge=1, le=100),
    before: datetime | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Conversation).filter(
        Conversation.owner_user_id == user.id,
        Conversation.archived_at.is_(None),
    )
    if before is not None:
        query = query.filter(Conversation.updated_at < before)
    return [_summary(row) for row in query.order_by(
        desc(Conversation.updated_at), desc(Conversation.id)
    ).limit(limit).all()]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, conversation_id, user)
    query = db.query(ConversationMessage).filter(ConversationMessage.conversation_id == row.id)
    if before_id is not None:
        query = query.filter(ConversationMessage.id < before_id)
    page = query.order_by(desc(ConversationMessage.id)).limit(limit + 1).all()
    has_more = len(page) > limit
    page = list(reversed(page[:limit]))
    from app.core.dependencies import verify_document_view_access
    from app.models.document import Document

    serialized: list[ConversationMessageOut] = []
    for message in page:
        data = ConversationMessageOut.model_validate(message, from_attributes=True).model_dump()
        safe_citations = []
        for citation in data.get("citation_metadata") or []:
            document = db.query(Document).filter(Document.id == citation.get("document_id")).first()
            if document is None:
                continue
            try:
                verify_document_view_access(document, user, db)
            except HTTPException:
                continue
            safe_citations.append(citation)
        data["citation_metadata"] = safe_citations or None
        request_metadata = data.get("request_metadata") or {}
        if request_metadata.get("document_ids"):
            permitted_ids = []
            for document_id in request_metadata["document_ids"]:
                document = db.query(Document).filter(Document.id == document_id).first()
                if document is None:
                    continue
                try:
                    verify_document_view_access(document, user, db)
                except HTTPException:
                    continue
                permitted_ids.append(document_id)
            request_metadata["document_ids"] = permitted_ids
            data["request_metadata"] = request_metadata
        serialized.append(ConversationMessageOut.model_validate(data))
    return ConversationDetail(
        **_summary(row).model_dump(),
        messages=serialized,
        next_before_id=page[0].id if has_more and page else None,
    )


@router.patch("/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: str,
    request: ConversationRename,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, conversation_id, user)
    row.title = re.sub(r"\s+", " ", request.title).strip()[:120]
    if not row.title:
        raise HTTPException(status_code=422, detail="A title is required")
    db.commit()
    db.refresh(row)
    return _summary(row)


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, conversation_id, user)
    db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == row.id
    ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/{conversation_id}/messages/stream")
def stream_message(
    conversation_id: str,
    request: ConversationMessageCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Persist each turn and stream sentence deltas accepted by RAG's evidence gates."""
    row = _owned(db, conversation_id, user)
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Message cannot be blank")

    with _active_lock:
        if row.id in _active_conversations:
            raise HTTPException(status_code=409, detail="Wait for the current answer before sending another message")

    request_key = (request.request_id or "").strip() or None
    if request_key:
        old = db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == row.id,
            ConversationMessage.idempotency_key == request_key,
            ConversationMessage.role == "user",
        ).first()
        if old:
            finished = db.query(ConversationMessage).filter(
                ConversationMessage.conversation_id == row.id,
                ConversationMessage.id > old.id,
                ConversationMessage.role == "assistant",
            ).order_by(ConversationMessage.id).first()
            if finished and finished.status == "completed":
                return StreamingResponse(
                    iter([_sse("accepted", {"request_id": request_key, "message_id": old.id, "replayed": True}),
                          _sse("delta", {"text": finished.content}),
                          _sse("complete", {"message": ConversationMessageOut.model_validate(finished, from_attributes=True).model_dump(mode="json"),
                                             "sources": finished.citation_metadata or [], "engine": finished.model_id})]),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
                )
            raise HTTPException(status_code=409, detail="This message is already being processed")

    prior = db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == row.id,
        ConversationMessage.status == "completed",
        ConversationMessage.role.in_(["user", "assistant"]),
    ).order_by(desc(ConversationMessage.id)).limit(8).all()
    history = [{"role": turn.role, "content": turn.content} for turn in reversed(prior)]
    if row.title == "New conversation" and not any(turn.role == "user" for turn in prior):
        title = re.sub(r"\s+", " ", content).strip()
        row.title = (title[:117] + "…") if len(title) > 120 else title[:120]

    user_message = ConversationMessage(
        conversation_id=row.id, role="user", content=content,
        status="completed", idempotency_key=request_key,
        request_metadata={"mode": request.mode, "document_ids": request.document_ids or []},
    )
    row.chat_mode = request.mode
    db.add(user_message)
    now = datetime.now(timezone.utc)
    row.last_message_at = now
    row.updated_at = now
    db.commit()
    with _active_lock:
        _active_conversations.add(row.id)
    db.refresh(user_message)
    message_id = user_message.id
    conversation_key = row.id
    owner_id = user.id
    request_key = request_key or str(message_id)
    db.expire_all()

    updates: queue.Queue[tuple[str, Any]] = queue.Queue()
    cancel = threading.Event()
    started = time.perf_counter()

    def produce() -> None:
        from app.db import session as db_session

        worker_db = db_session.SessionLocal()
        try:
            worker_user = worker_db.query(User).filter(User.id == owner_id, User.is_active.is_(True)).first()
            worker_conversation = worker_db.query(Conversation).filter(
                Conversation.id == conversation_key,
                Conversation.owner_user_id == owner_id,
            ).first()
            if worker_user is None or worker_conversation is None:
                raise RuntimeError("Conversation access changed while the request was starting")
            updates.put(("status", {"stage": "retrieving" if request.mode == "documents" else "generating"}))

            def send_verified_delta(text: str) -> None:
                if cancel.is_set():
                    raise InterruptedError("Client disconnected")
                if text:
                    updates.put(("delta", text))

            if request.mode == "general":
                from app.services.llm_service import generate_general_answer
                generated = generate_general_answer(content, history, send_verified_delta)
                response = {
                    "answer": generated.text,
                    "engine": "llm_ollama_general",
                    "sources": [],
                    "has_evidence": False,
                    "confidence": 0.0,
                    "notes": f"Generated by local model {generated.model}; no documents were searched.",
                    "model": generated.model,
                    "generation_ms": generated.latency_ms,
                }
            else:
                from app.services.rag_service import answer_question
                rag_response = answer_question(
                    worker_db,
                    ChatQueryRequest(
                        question=content,
                        document_ids=request.document_ids,
                        history=history,
                    ),
                    user=worker_user,
                    on_delta=send_verified_delta,
                )
                response = {
                    "answer": rag_response.answer,
                    "engine": rag_response.engine,
                    "sources": rag_response.sources,
                    "has_evidence": rag_response.has_evidence,
                    "confidence": rag_response.confidence,
                    "notes": rag_response.notes,
                    "model": rag_response.model_id or rag_response.engine,
                    "generation_ms": int((time.perf_counter() - started) * 1000),
                }
            if cancel.is_set():
                assistant = ConversationMessage(
                    conversation_id=conversation_key, role="assistant", content="",
                    status="interrupted", error_metadata={"message": "Generation stopped by the client."},
                    generation_ms=int((time.perf_counter() - started) * 1000),
                )
                worker_db.add(assistant)
                worker_db.commit()
                return
            assistant = ConversationMessage(
                conversation_id=conversation_key,
                role="assistant",
                content=response["answer"],
                status="completed",
                model_id=response["model"],
                citation_metadata=[source.model_dump() if hasattr(source, "model_dump") else source for source in response["sources"]],
                usage_metadata={
                    "has_evidence": response["has_evidence"],
                    "confidence": response["confidence"],
                    "engine": response["engine"],
                    "notes": response["notes"],
                },
                generation_ms=response["generation_ms"],
            )
            worker_db.add(assistant)
            worker_conversation.last_message_at = datetime.now(timezone.utc)
            worker_conversation.updated_at = worker_conversation.last_message_at
            worker_db.commit()
            worker_db.refresh(assistant)
            logger.info(
                "chat completed user=%s conversation=%s request=%s engine=%s generation_ms=%s",
                owner_id, conversation_key, request_key, response["engine"], assistant.generation_ms,
            )
            updates.put(("complete", {
                "message": ConversationMessageOut.model_validate(assistant, from_attributes=True).model_dump(mode="json"),
                "engine": response["engine"],
                "notes": response["notes"],
                "sources": [source.model_dump() if hasattr(source, "model_dump") else source for source in response["sources"]],
            }))
        except Exception as exc:  # noqa: BLE001
            worker_db.rollback()
            if cancel.is_set():
                status = "interrupted"
                message = "Generation stopped by the client."
            else:
                status = "failed"
                message = "The answer could not be generated. Try again."
                logger.exception("chat failed user=%s conversation=%s request=%s", owner_id, conversation_key, request_key)
            failed = ConversationMessage(
                conversation_id=conversation_key, role="assistant", content="",
                status=status, error_metadata={"message": message},
                generation_ms=int((time.perf_counter() - started) * 1000),
            )
            worker_db.add(failed)
            worker_db.commit()
            if not cancel.is_set():
                updates.put(("error", {"message": message, "request_id": request_key}))
        finally:
            worker_db.close()
            with _active_lock:
                _active_conversations.discard(conversation_key)
            updates.put(("end", None))

    worker = threading.Thread(target=produce, name="docsentinel-chat-generation", daemon=True)

    def events():
        yield _sse("accepted", {"request_id": request_key, "message_id": message_id})
        worker.start()
        try:
            while True:
                kind, payload = updates.get()
                if kind == "end":
                    break
                yield _sse(kind, {"text": payload} if kind == "delta" else payload)
        finally:
            cancel.set()

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )

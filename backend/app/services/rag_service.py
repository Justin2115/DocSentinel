import logging
import os
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.schemas.query import ChatQueryRequest, ChatQueryResponse, SourceCitation
from app.services.embedding_service import encode_texts, semantic_search

logger = logging.getLogger(__name__)

# Empirical minimum similarity score to consider retrieved passages as relevant evidence
EVIDENCE_THRESHOLD = 0.25

# Patterns for factual entity extraction in local fallback
_CURRENCY_RE = re.compile(
    r"(?:[\$€£₹]\s*[\d,]+(?:\.\d+)?|[\d,]+(?:\.\d+)?\s*(?:dollars|rupees|cents|usd|inr|eur|gbp)|(?:\d+%\s*(?:per\s+month|annually|penalty)?))",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
    re.IGNORECASE,
)
_IDENTIFIER_RE = re.compile(
    r"\b(?:INV-[A-Za-z0-9-]+|ACC\s*#?\s*[A-Za-z0-9\s]{4,20}|ID-[A-Za-z0-9-]+)\b",
    re.IGNORECASE,
)


def _split_multipart_question(question: str) -> list[str]:
    """Split compound or multi-part questions into individual query clauses."""
    cleaned = (question or "").strip()
    if not cleaned:
        return []

    # Split by multiple question marks first
    parts = [p.strip() for p in re.split(r"\?+", cleaned) if p.strip()]
    if len(parts) > 1:
        return parts

    # Split by explicit conjunctions joining question clauses
    conjunction_split = re.split(
        r"\s+(?:and|also|as well as)\s+(?=(?:what|when|where|how|who|which|is|are|can|does|did)\b)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if len(conjunction_split) > 1:
        return [p.strip().rstrip("?").strip() for p in conjunction_split if p.strip()]

    return [cleaned]


def _extract_sentences(text: str) -> list[str]:
    """Break text into individual sentence / clause statements, pairing key-value OCR lines."""
    if not text:
        return []
    raw_lines = [l.strip() for l in re.split(r"[\n\r]+", text) if l.strip()]
    merged_lines: list[str] = []
    i = 0
    while i < len(raw_lines):
        curr = raw_lines[i]
        if i + 1 < len(raw_lines):
            nxt = raw_lines[i + 1]
            is_kv = False
            if any(curr.lower().endswith(w) for w in ["number", "date", "due", "bank", "from", "to", "total", "tax", "fee", "fees"]):
                is_kv = True
            elif len(curr) < 30 and not curr.endswith((".", "!", "?")) and len(nxt) < 60:
                if re.search(r"[\$€£₹\d]", nxt):
                    is_kv = True
            if is_kv:
                merged_lines.append(f"{curr}: {nxt}")
                i += 2
                continue
        merged_lines.append(curr)
        i += 1

    sentences = []
    for line in merged_lines:
        for s in re.split(r"(?<=[.!?।॥])\s+", line):
            cleaned = s.strip()
            if len(cleaned) >= 5:
                sentences.append(cleaned)
    return sentences


def _score_sentence_for_query(sentence: str, query: str) -> float:
    """Compute token overlap and keyword affinity between sentence and query clause."""
    s_lower = sentence.lower()
    q_tokens = [w for w in re.findall(r"[\w\u0900-\u097F]+", query.lower()) if len(w) >= 3]
    if not q_tokens:
        return 0.0

    matches = sum(1 for t in q_tokens if t in s_lower)
    score = matches / len(q_tokens)

    # Bonus for exact phrase matches
    if query.lower() in s_lower:
        score += 0.5
    return score


def _extract_single_answer(
    sub_q: str,
    chunks: list[dict[str, Any]],
) -> tuple[str, Optional[dict[str, Any]]]:
    """Extract a direct factual answer for a single sub-question from candidate chunks."""
    sub_lower = sub_q.lower()
    best_sentence: Optional[str] = None
    best_chunk: Optional[dict[str, Any]] = None
    best_score = -1.0

    # Collect candidate sentences across all authorized chunks
    for chunk in chunks:
        sentences = _extract_sentences(chunk["snippet"])
        for s in sentences:
            score = _score_sentence_for_query(s, sub_q)

            # Intent-specific boosters
            if any(k in sub_lower for k in ["how much", "total", "owe", "amount", "cost", "fee", "price"]):
                if _CURRENCY_RE.search(s):
                    score += 0.60
                if "total" in s.lower() or "owe" in s.lower():
                    score += 0.30

            if any(k in sub_lower for k in ["when", "date", "deadline", "period"]):
                if _DATE_RE.search(s):
                    score += 0.60
                if "due date" in s.lower() or "deadline" in s.lower() or "payment is due" in s.lower():
                    score += 0.35

            if any(k in sub_lower for k in ["invoice", "number", "inv", "account", "acc", "id"]):
                if _IDENTIFIER_RE.search(s):
                    score += 0.60
                if "number" in s.lower() or "inv" in s.lower():
                    score += 0.30

            if any(k in sub_lower for k in ["late", "penalty"]):
                if "late" in s.lower() or "fee" in s.lower() or "%" in s:
                    score += 0.50

            if any(k in sub_lower for k in ["bank", "pay to", "payable", "vendor", "who"]):
                if "bank" in s.lower() or "acc" in s.lower():
                    score += 0.40

            if score > best_score:
                best_score = score
                best_sentence = s
                best_chunk = chunk

    if best_sentence and best_score >= 0.20:
        return best_sentence, best_chunk

    return "", None


def _format_local_extractive_answer(
    question: str,
    sub_questions: list[str],
    answers_with_chunks: list[tuple[str, str, Optional[dict[str, Any]]]],
) -> tuple[str, bool]:
    """Format extracted answers into a cohesive, grounded response."""
    valid_answers = [
        (q, ans, ch) for q, ans, ch in answers_with_chunks if ans and ch
    ]

    if not valid_answers:
        return (
            "The available authorized documents do not contain information to answer this question.",
            False,
        )

    if len(valid_answers) == 1 and len(sub_questions) == 1:
        _, ans, ch = valid_answers[0]
        doc_name = ch["document_name"]
        page = ch.get("page_number")
        page_str = f" (Page {page})" if page else ""
        return f"Based on {doc_name}{page_str}:\n{ans}", True

    # Multi-part response
    lines = ["Based on the retrieved authorized documents:"]
    for q, ans, ch in valid_answers:
        doc_name = ch["document_name"]
        page = ch.get("page_number")
        page_str = f" (Page {page})" if page else ""
        lines.append(f"- **{q.strip('?')}**: {ans} — *[{doc_name}{page_str}]*")

    # If some sub-questions were not found
    missing_count = len(sub_questions) - len(valid_answers)
    if missing_count > 0:
        lines.append(
            "\n*(Note: Some parts of the question could not be verified from the available documents.)*"
        )

    return "\n".join(lines), True


def answer_question(
    db: Session,
    request: ChatQueryRequest,
    user: Any | None = None,
) -> ChatQueryResponse:
    """Answer a user's natural language question grounded in authorized document evidence."""
    clean_q = (request.question or "").strip()
    if not clean_q:
        return ChatQueryResponse(
            answer="Please provide a question to search your documents.",
            sources=[],
            confidence=0.0,
            question=clean_q,
            has_evidence=False,
            engine="none",
        )

    # 1. Retrieve passage candidates using multilingual vector search with chunk-level granularity
    try:
        search_res = semantic_search(
            db,
            clean_q,
            top_k=8,
            min_score=0.18,
            user=user,
            aggregate_by_document=False,  # chunk-level for RAG
        )
        hits = search_res.items
    except Exception:
        logger.exception("RAG passage retrieval failed for question %r", clean_q)
        hits = []

    # Filter to specific document if requested
    if request.document_id is not None:
        hits = [h for h in hits if h.document_id == request.document_id]

    # 2. Evidence sufficiency check
    top_similarity = max([h.similarity for h in hits], default=0.0)
    if not hits or top_similarity < EVIDENCE_THRESHOLD:
        return ChatQueryResponse(
            answer="The available authorized documents do not contain information to answer this question.",
            sources=[],
            confidence=0.0,
            question=clean_q,
            has_evidence=False,
            engine="no_evidence",
        )

    # Build chunk dictionary objects
    chunk_dicts = [
        {
            "document_id": h.document_id,
            "document_name": h.document_name,
            "page_number": h.page_number,
            "chunk_index": h.chunk_index,
            "snippet": h.snippet,
            "similarity": h.similarity,
        }
        for h in hits
    ]

    # 3. Check for external LLM API keys
    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if gemini_key or openai_key:
        try:
            return _call_external_llm(
                clean_q, chunk_dicts, top_similarity, gemini_key, openai_key
            )
        except Exception:
            logger.exception("External LLM generation failed; falling back to local synthesis")

    # 4. Zero-Cost Local Extractive Fallback
    sub_questions = _split_multipart_question(clean_q)
    extracted_answers: list[tuple[str, str, Optional[dict[str, Any]]]] = []
    used_chunks: list[dict[str, Any]] = []

    for sub_q in sub_questions:
        ans, best_chunk = _extract_single_answer(sub_q, chunk_dicts)
        extracted_answers.append((sub_q, ans, best_chunk))
        if best_chunk and best_chunk not in used_chunks:
            used_chunks.append(best_chunk)

    answer_text, has_evidence = _format_local_extractive_answer(
        clean_q, sub_questions, extracted_answers
    )

    if not has_evidence:
        return ChatQueryResponse(
            answer="The available authorized documents do not contain information to answer this question.",
            sources=[],
            confidence=0.0,
            question=clean_q,
            has_evidence=False,
            engine="extractive_fallback",
        )

    # Deduplicate sources by (document_id, page_number)
    seen_sources = set()
    citations: list[SourceCitation] = []
    for c in used_chunks or chunk_dicts[:3]:
        key = (c["document_id"], c.get("page_number"))
        if key in seen_sources:
            continue
        seen_sources.add(key)
        citations.append(
            SourceCitation(
                document_id=c["document_id"],
                document_name=c["document_name"],
                page_number=c.get("page_number"),
                snippet=c["snippet"][:250],
                similarity=round(c["similarity"], 4),
            )
        )

    return ChatQueryResponse(
        answer=answer_text,
        sources=citations,
        confidence=round(top_similarity, 4),
        question=clean_q,
        has_evidence=True,
        engine="extractive_fallback",
        notes="Grounded in retrieved authorized passages via local extractive synthesis.",
    )


def _call_external_llm(
    question: str,
    chunks: list[dict[str, Any]],
    confidence: float,
    gemini_key: Optional[str],
    openai_key: Optional[str],
) -> ChatQueryResponse:
    """Call external LLM when configured in environment."""
    context_blocks = []
    for i, c in enumerate(chunks[:5], 1):
        context_blocks.append(
            f"Passage {i} (Document: {c['document_name']}, Page: {c.get('page_number', 1)}):\n{c['snippet']}"
        )
    context_str = "\n\n".join(context_blocks)

    system_prompt = (
        "You are DocSentinel AI, an assistant for document Q&A. "
        "Answer the user's question accurately, concisely, and strictly based ONLY on the provided passages. "
        "Cite the document name and page number for each fact. "
        "If the answer cannot be determined from the provided passages, clearly state: "
        "'The available authorized documents do not contain information to answer this question.'"
    )

    prompt = f"Passages:\n{context_str}\n\nQuestion: {question}\n\nAnswer:"

    if gemini_key:
        import urllib.request
        import json

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"{system_prompt}\n\n{prompt}"}
                    ]
                }
            ]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )
            engine = "llm_gemini"
    else:
        import urllib.request
        import json

        url = "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {openai_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = data["choices"][0]["message"]["content"].strip()
            engine = "llm_openai"

    citations = [
        SourceCitation(
            document_id=c["document_id"],
            document_name=c["document_name"],
            page_number=c.get("page_number"),
            snippet=c["snippet"][:250],
            similarity=round(c["similarity"], 4),
        )
        for c in chunks[:3]
    ]

    has_ev = "not contain" not in answer.lower()
    return ChatQueryResponse(
        answer=answer,
        sources=citations if has_ev else [],
        confidence=round(confidence, 4),
        question=question,
        has_evidence=has_ev,
        engine=engine,
        notes="Generated using configured external LLM provider.",
    )

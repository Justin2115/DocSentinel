import logging
import re
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy.orm import Session

from app.schemas.query import ChatQueryRequest, ChatQueryResponse, SourceCitation
from app.core.config import settings

logger = logging.getLogger(__name__)

# Minimum RAW cosine similarity for retrieved evidence to count as support.
# Calibrated on a labeled query set: hard-negative queries maxed at 0.252,
# relevant queries median 0.459 (see settings.SEMANTIC_MIN_SCORE).
# Retrieval threshold only — not an answer-correctness probability.
EVIDENCE_THRESHOLD = settings.SEMANTIC_MIN_SCORE

# Patterns for factual entity extraction in local fallback
_CURRENCY_RE = re.compile(
    r"(?:[\$€£₹]\s*[\d,]+(?:\.\d+)?|[\d,]+(?:\.\d+)?\s*(?:dollars|rupees|cents|usd|inr|eur|gbp)|(?:\d+%\s*(?:per\s+month|annually|penalty)?))",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|(?:दिनांक|तारीख)\s*[:#-]?\s*\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})",
    re.IGNORECASE,
)
_IDENTIFIER_RE = re.compile(
    r"(?:\b(?:INV|BILL|REF|ACC|ID|PO)[-_\s#:]+[A-Za-z0-9-]+|\b(?:INVOICE|ACCOUNT|REFERENCE|APPLICATION|BILL)\s*(?:NO\.?|NUMBER|#)?\s*[:#-]?\s*[A-Za-z0-9-]+|(?:चालान\s*(?:संख्या|क्रमांक|नं\.?)|बिल\s*(?:क्रमांक|नं\.?)|क्रमांक)\s*[:#-]?\s*[A-Za-z0-9-]+|\bINV-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)

_QUERY_STOPWORDS = {
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "can", "could", "should", "would",
    "shall", "will", "may", "might", "must", "and", "or", "but", "in", "on", "at",
    "to", "for", "of", "with", "by", "from", "up", "about", "into", "over", "after",
    "it", "its", "they", "them", "their", "this", "that", "these", "those",
    "tell", "show", "give", "please", "find",
}

_DOC_GENERIC_WORDS = {"document", "file", "form", "page", "record", "sample", "pdf"}

# A question asking what a document is about uses only generic wording, so no
# passage can match it. It is recognised here because the answer is different in
# kind: the document itself is the evidence, not a line inside it.
_OVERVIEW_RE = re.compile(
    r"\bwhat\s+(?:is|are|was|were)\s+(?:actually\s+)?(?:this|that|the|these|those|it|its)?"
    r"\s*(?:document|file|pdf|page|certificate|receipt|form|record|it)?\s+about\b"
    r"|\bwhat\s+(?:is|are)\s+the\s+(?:main|primary|overall|general)?"
    r"\s*(?:subject|topic|purpose|summary|title|theme)\b"
    r"|\b(?:summarise|summarize|summary|overview|describe)\b(?:\s+\w+){0,6}?"
    r"\s+\b(?:document|file|pdf|certificate|page|this)\b"
    r"|\bwhat\s+(?:does|do|did)\s+(?:this|the|it)\s+"
    r"(?:document|file|page|certificate)?\s*(?:say|contain|mention)\b",
    re.IGNORECASE,
)


def _is_overview_question(question: str) -> bool:
    """True when the question asks what a document is about rather than for a value."""
    return bool(_OVERVIEW_RE.search((question or "").strip()))


def _is_application_metadata_question(question: str) -> bool:
    """Detect application-level questions about document counts, statuses, or listings."""
    ql = (question or "").lower().strip()
    patterns = [
        r"\b(?:how many|number of|total count|count of)\b.*\b(?:documents|files|records|uploads|pdfs)\b",
        r"\bhow many\b.*\b(?:uploaded|in the system|stored|available)\b",
        r"\b(?:what|which|list|show)\b.*\b(?:documents|files|uploads)\b.*\b(?:uploaded|available|in the system|stored|do i have)\b",
        r"^(?:list|show)\s+(?:all\s+)?(?:my\s+)?(?:documents|files|uploads)\b",
    ]
    return any(re.search(pat, ql) for pat in patterns)


def _answer_application_metadata_question(
    db: Session,
    question: str,
    user: Any | None = None,
    allowed_ids: Optional[Sequence[int]] = None,
) -> ChatQueryResponse:
    """Answer application/catalog metadata questions from authorized SQL data.

    ``allowed_ids`` is the scope the user selected with the document picker. A
    catalog answer reads the document table instead of the evidence pool, so it
    has to apply that scope itself or it names documents the question excluded.
    """
    from sqlalchemy import func, or_
    from app.core.rbac import UserRole
    from app.models.document import Document

    query = db.query(Document)
    if allowed_ids:
        query = query.filter(Document.id.in_([int(value) for value in allowed_ids]))
    if user is not None:
        from app.core.rbac import UserRole
        if not UserRole.is_super_admin(getattr(user, "role", None)):
            user_dept = (getattr(user, "department", None) or "").strip().upper()
            if user_dept:
                query = query.filter(func.upper(Document.department) == user_dept)
            else:
                query = query.filter(Document.id == -1)

    docs = query.order_by(Document.uploaded_at.desc()).all()
    total_count = len(docs)
    completed_count = sum(
        1 for d in docs
        if (d.status or "").lower() in ("completed", "approved", "indexed")
    )
    pending_count = sum(
        1 for d in docs
        if "pending" in (d.status or "").lower()
        or "needs" in (d.status or "").lower()
    )

    doc_names = [d.original_filename for d in docs if d.original_filename]
    unique_names = list(dict.fromkeys(doc_names))
    scoped = bool(allowed_ids)

    if total_count == 0:
        ans = (
            "None of the documents you selected are in the catalog, so 0 documents "
            "are described here."
            if scoped
            else "There are currently 0 authorized documents uploaded in DocSentinel."
        )
    else:
        sample_str = ", ".join(unique_names[:5])
        if len(unique_names) > 5:
            sample_str += f", and {len(unique_names) - 5} more"
        ans = (
            f"The {total_count} selected document(s) "
            f"({completed_count} processed, {pending_count} pending review): "
            f"{sample_str}."
            if scoped
            else (
                f"There are currently {total_count} authorized document(s) uploaded in DocSentinel "
                f"({completed_count} processed, {pending_count} pending review). "
                f"Available documents include: {sample_str}."
            )
        )

    return ChatQueryResponse(
        answer=ans,
        sources=[],
        confidence=1.0,
        question=question,
        has_evidence=True,
        engine="system_metadata",
        notes=(
            "Grounded in the document catalog rows for your current selection."
            if scoped
            else "Grounded in system document catalog metadata."
        ),
    )


def _get_target_attribute_info(
    question: str,
) -> tuple[Optional[str], Optional[str], bool]:
    """Extract target attribute, subject entity, and whether a collection is requested."""
    ql = question.lower().strip().rstrip("?")
    entity_name: Optional[str] = None
    target_attr: Optional[str] = None
    is_collection = False

    pos = re.search(r"\b([A-Za-z\s]+?)(?:'s|’s)\s+([a-z0-9_\s]+)", question)
    if pos:
        entity_name = pos.group(1).strip()
        target_attr = pos.group(2).strip().lower()
    else:
        m = re.search(
            r"(?:what|who|which)\s+(?:is|are|was|were)?\s*(?:the\s+)?([a-z0-9_\s]{2,30}?)(?:\s+(?:of|for|in|from|about)\s+([A-Za-z0-9\s]+)|$)",
            ql,
        )
        if m:
            target_attr = m.group(1).strip()
            target_attr = re.sub(r"^(?:name of|value of)\s+", "", target_attr)
            if m.group(2):
                entity_name = m.group(2).strip()

    if target_attr and any(
        k in target_attr
        for k in ["information", "details", "data", "summary", "profile", "overview"]
    ):
        is_collection = True

    return target_attr, entity_name, is_collection


def _split_multipart_question(question: str) -> list[str]:
    """Split compound or multi-part questions into individual query clauses."""
    cleaned = (question or "").strip()
    if not cleaned:
        return []

    parts = [p.strip() for p in re.split(r"\?+", cleaned) if p.strip()]
    if len(parts) > 1:
        return parts

    conjunction_split = re.split(
        r"\s+(?:and|also|as well as|और|तथा|आणि|व)\s+(?=(?:what|when|where|how|who|which|is|are|can|does|did|क्या|कब|कहाँ|कितना|कितनी|कौन|काय|केव्हा|कुठे|किती|कोण|कोणती)\b)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if len(conjunction_split) > 1:
        return [
            p.strip().rstrip("?").strip()
            for p in conjunction_split if p.strip()
        ]

    return [cleaned]


_SENTENCE_TERMINATOR = re.compile(r"[.!?।॥:]\s*$")
_LOWERCASE_START = re.compile(r"[a-zà-˿]")


def _join_wrapped_lines(lines: list[str]) -> list[str]:
    """Re-join an OCR line that is only half a sentence with the line ending it.

    OCR emits one text box per detected line, so a sentence the typesetter wrapped
    arrives split with no punctuation on the first part. Treating each line as a
    whole thought is what made an evidence sentence stop at "in accordance with the
    laws" while "of the Republic of Indonesia." sat in the next one. Continuation is
    recognised structurally, never by content: the previous line carries no sentence
    terminator and the next one begins in lower case, so a heading, a numbered item
    or a label keeps its own boundary.
    """
    joined: list[str] = []
    for line in lines:
        if (
            joined
            and not _SENTENCE_TERMINATOR.search(joined[-1])
            and _LOWERCASE_START.match(line)
        ):
            joined[-1] = f"{joined[-1]} {line}"
            continue
        joined.append(line)
    return joined


def _extract_sentences(text: str) -> list[str]:
    """Break text into sentences or clauses, pairing key-value OCR lines."""
    if not text:
        return []
    raw_lines = _join_wrapped_lines(
        [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
    )
    merged_lines: list[str] = []
    i = 0

    while i < len(raw_lines):
        curr = raw_lines[i]
        if i + 1 < len(raw_lines) and ":" not in curr:
            nxt = raw_lines[i + 1]
            is_kv = False
            if any(
                curr.lower().endswith(w)
                for w in [
                    "number", "date", "due", "bank", "from", "to", "total",
                    "tax", "fee", "fees", "संख्या", "क्रमांक", "दिनांक",
                    "तारीख", "रक्कम", "राशी", "नाव", "पत्ता",
                ]
            ):
                is_kv = True
            elif (
                len(curr) < 30
                and not curr.endswith((".", "!", "?"))
                and len(nxt) < 60
                and ":" not in nxt
                and re.search(r"[\$€£₹\d]", nxt)
            ):
                is_kv = True

            if is_kv:
                merged_lines.append(f"{curr}: {nxt}")
                i += 2
                continue

        merged_lines.append(curr)
        i += 1

    sentences = []
    for line in merged_lines:
        for sentence in re.split(r"(?<=[.!?।॥])\s+", line):
            cleaned = sentence.strip()
            if len(cleaned) >= 5:
                sentences.append(cleaned)
    return sentences


def _score_sentence_for_query(sentence: str, query: str) -> float:
    """Compute token overlap and keyword affinity between sentence and query clause."""
    s_lower = sentence.lower()
    raw_tokens = [
        w for w in re.findall(r"[\w\u0900-\u097F]+", query.lower())
        if len(w) >= 2
    ]
    content_tokens = [w for w in raw_tokens if w not in _QUERY_STOPWORDS]
    tokens = content_tokens if content_tokens else raw_tokens
    if not tokens:
        return 0.0

    matches = sum(1 for token in tokens if token in s_lower)
    score = matches / len(tokens)

    if ":" in sentence:
        key_part = sentence.split(":", 1)[0].strip().lower()
        if any(t in key_part for t in tokens if t not in _DOC_GENERIC_WORDS):
            score += 0.40

    if query.lower() in s_lower:
        score += 0.5
    return score


def _extract_single_answer(
    sub_q: str,
    chunks: list[dict[str, Any]],
) -> tuple[str, Optional[dict[str, Any]]]:
    """Extract a direct factual answer for a single sub-question."""
    sub_lower = sub_q.lower()
    target_attr, entity_name, is_collection = _get_target_attribute_info(sub_q)

    best_sentence: Optional[str] = None
    best_chunk: Optional[dict[str, Any]] = None
    best_score = -1.0

    if is_collection and target_attr:
        stem_attr = re.sub(
            r"\b(?:information|details|data|summary|profile|overview)\b",
            "",
            target_attr,
        ).strip()
        for chunk in chunks:
            sentences = _extract_sentences(chunk["snippet"])
            for idx, sentence in enumerate(sentences):
                sentence_lower = sentence.lower()
                if (
                    (stem_attr and stem_attr in sentence_lower)
                    or ("information" in sentence_lower and "|" in sentence)
                ):
                    details: list[str] = []
                    for follow in sentences[idx + 1: idx + 8]:
                        if ":" in follow or "|" in follow:
                            details.append(follow)
                        elif len(details) >= 2:
                            break
                    if details:
                        header_name = (
                            sentence.split("|")[0].strip()
                            if "|" in sentence else sentence
                        )
                        full_ans = f"{header_name}:\n" + "\n".join(
                            f"- {detail}" for detail in details
                        )
                        return full_ans, chunk

    if entity_name and target_attr:
        ent_lower = entity_name.lower()
        attr_words = [
            w for w in target_attr.split()
            if w not in _QUERY_STOPWORDS and w not in _DOC_GENERIC_WORDS
        ]
        for chunk in chunks:
            sentences = _extract_sentences(chunk["snippet"])
            for idx, sentence in enumerate(sentences):
                if ent_lower in sentence.lower():
                    for adj_idx in [idx, idx + 1, idx + 2, idx - 1]:
                        if 0 <= adj_idx < len(sentences):
                            adj_sentence = sentences[adj_idx]
                            if ":" in adj_sentence:
                                key = adj_sentence.split(":", 1)[0].strip().lower()
                                if any(word in key for word in attr_words):
                                    answer = (
                                        f"{sentence} — {adj_sentence}"
                                        if adj_idx != idx else adj_sentence
                                    )
                                    return answer, chunk

    if target_attr and not is_collection:
        attr_words = [
            w for w in target_attr.split()
            if w not in _QUERY_STOPWORDS and w not in _DOC_GENERIC_WORDS
        ]
        if attr_words:
            for chunk in chunks:
                for sentence in _extract_sentences(chunk["snippet"]):
                    if ":" in sentence:
                        key = sentence.split(":", 1)[0].strip().lower()

                        if all(word in key for word in attr_words):
                            return sentence, chunk


    for chunk in chunks:
        for sentence in _extract_sentences(chunk["snippet"]):
            score = _score_sentence_for_query(sentence, sub_q)


            if any(k in sub_lower for k in [
                "how much", "total", "owe", "amount", "cost", "fee", "price",
                "due", "balance", "pay", "कितना", "कितनी", "कुल", "राशि",
                "रकम", "मूल्य", "लागत", "बकाया", "देय", "किती", "एकूण",
                "रक्कम", "देयक", "बाकी", "पैसे",
            ]):
                if _CURRENCY_RE.search(sentence):
                    query_tokens = [
                        token
                        for token in re.findall(
                            r"[\w\u0900-\u097F]+", sub_lower
                        )
                        if len(token) >= 3 and token not in _QUERY_STOPWORDS
                    ]
                    sentence_lower = sentence.lower()
                    item_tokens = [
                        token for token in query_tokens
                        if token not in {
                            "much", "total", "amount", "cost", "price", "fee",
                            "walking", "shower", "pay", "owe", "balance", "due",
                        }
                    ]

                    matched_item = bool(item_tokens) and any(
                        token in sentence_lower for token in item_tokens
                    )
                    if matched_item:
                        score += 0.80
                    elif any(w in sentence_lower for w in [
                        "total", "owe", "कुल", "एकूण", "रक्कम", "राशि",
                        "देय", "देयक",
                    ]):
                        score += 0.30

            if any(k in sub_lower for k in [
                "when", "date", "deadline", "period", "due date", "payment due",
                "कब", "दिनांक", "तारीख", "समय", "अवधि", "अंतिम", "केव्हा",
                "वेळ", "मुदत", "शेवटची",
            ]):
                if _DATE_RE.search(sentence):
                    score += 0.60
                if any(w in sentence.lower() for w in [
                    "due date", "deadline", "payment is due", "दिनांक",
                    "तारीख", "शेवटची तारीख", "अंतिम",
                ]):
                    score += 0.35


            if any(k in sub_lower for k in [
                    "invoice", "number", "inv", "account", "acc", "id", "bill",
                    "ref", "reference", "चालान", "संख्या", "क्रमांक", "बिल",
                    "खाता", "बिलाचा", "खाते",
                ]):
                    if _IDENTIFIER_RE.search(sentence):
                        score += 0.60
                    if any(w in sentence.lower() for w in [
                        "number", "inv", "संख्या", "क्रमांक", "चालान", "बिल",
                    ]):
                        score += 0.30

            if any(k in sub_lower for k in [
                    "late", "penalty", "interest", "charge", "विलंब", "जुर्माना",
                    "व्याज", "दंड", "उशीर",
                ]):
                    if any(w in sentence.lower() for w in [
                        "late", "fee", "विलंब", "दंड", "शुल्क", "व्याज",
                    ]) or "%" in sentence:
                        score += 0.50

            if any(k in sub_lower for k in [
                    "bank", "pay to", "payable", "vendor", "who", "recipient",
                    "company", "बैंक", "बँक", "किसे", "कोणाला",
                ]):
                    if any(w in sentence.lower() for w in [
                        "bank", "acc", "बँक", "बैंक", "खाते", "खाता",
                    ]):
                        score += 0.40

            if score > best_score:
                best_score = score
                best_sentence = sentence
                best_chunk = chunk

    if best_sentence and best_score >= 0.20:
        return best_sentence, best_chunk

    return "", None


_ANSWER_UNIT_LIMIT = 8

# How close a second reading of the same value has to score before the source
# counts as genuinely ambiguous for this question.
_AMBIGUITY_MARGIN = 0.12

_NO_EVIDENCE_TEXT = (
    "The available authorized documents do not contain information to answer "
    "this question."
)


def _evidence_confidence(plan: Any, units: list[Any]) -> float:
    """A retrieval-evidence score, not a probability that the answer is right.

    Semantic support is the calibrated raw cosine similarity. Exact text support
    is reported on a comparable scale so the UI can sort answers, and the notes
    field says in words what the number came from.
    """
    if not units:
        return 0.0
    semantic = max((unit.semantic_raw for unit in units), default=0.0)
    if semantic >= EVIDENCE_THRESHOLD:
        return round(semantic, 4)
    best_coverage = max((unit.coverage for unit in units), default=0.0)
    return round(min(0.85, 0.4 + 0.45 * best_coverage), 4)


def _unit_citation(unit: Any, plan: Any) -> SourceCitation:
    value = unit.requested_value(plan) if plan.value_kind else None
    columns = [cell for cell in unit.header if cell]
    return SourceCitation(
        document_id=unit.document_id,
        document_name=unit.document_name,
        page_number=unit.page_number,
        snippet=unit.citation_snippet(250),
        similarity=round(unit.semantic_raw, 4) if unit.semantic_raw > 0 else None,
        support=unit.support_reason(plan),
        source_kind=unit.source_kind,
        table_columns=columns or None,
        line_index=unit.line_index if unit.line_index >= 0 else None,
        value_state=value.state if value else None,
    )


def _citations(units: Iterable[Any], plan: Any, limit: int = 6) -> list[SourceCitation]:
    seen: set[tuple[int, int]] = set()
    out: list[SourceCitation] = []
    for unit in units:
        key = (unit.document_id, unit.page_number)
        if key in seen and len(out) >= limit:
            continue
        seen.add(key)
        out.append(_unit_citation(unit, plan))
        if len(out) >= limit:
            break
    return out


def _value_line(unit: Any, plan: Any) -> str:
    """Quote the window with its label, so an amount never travels alone."""
    label_lines = [line for line in unit.lines[:-1] if line.strip()]
    label = " ".join(label_lines[-3:]).strip()
    if unit.header:
        columns = " | ".join(cell for cell in unit.header if cell)
        label = f"{label} (table columns: {columns})" if label else columns
    return label or unit.lines[-1]


def _compose_deterministic(plan: Any, units: list[Any]) -> tuple[Optional[str], list[Any], Optional[str]]:
    """Build the answer from evidence without a language model.

    Returns (answer, units used, abstention text). Exact amounts, dates and
    identifiers are produced here rather than by a model, because a model can
    paraphrase a damaged Devanagari digit into a number that was never legible on
    the page. When two clean values compete inside the same document the source
    does not support a choice, so the abstention names the page instead of
    guessing.
    """
    from app.services import evidence_service as ev

    if not units:
        return None, [], None

    if plan.value_kind:
        if plan.aggregate:
            pairs = ev.aggregate_values(units, plan.value_kind, per_document_limit=8)
            if not pairs:
                return None, [], None
            lines: list[str] = []
            used: list[Any] = []
            for value, unit in pairs:
                context = _value_line(unit, plan)
                suffix = f" in {context}" if context and context not in value.raw else ""
                lines.append(
                    f"- {value.display}{suffix} — {unit.document_name}, page {unit.page_number}"
                )
                if unit not in used:
                    used.append(unit)
            header = (
                f"Found {len(pairs)} {plan.value_kind} reading"
                f"{'s' if len(pairs) != 1 else ''} in the authorized documents:"
            )
            return f"{header}\n" + "\n".join(lines), used[:_ANSWER_UNIT_LIMIT], None

        ranked = [unit for unit in units if unit.requested_value(plan) is not None]
        if ranked:
            best = ranked[0]
            value = best.requested_value(plan)
            best_score = ev.unit_rank_score(best, plan)
            # Units arrive ranked, so a competitor stops being a competitor once
            # its evidence score falls clearly below the winner's. A second amount
            # that matches nothing in the question is page noise, not ambiguity.
            competitors = [
                other
                for other in ranked[1:]
                if (other_value := other.requested_value(plan)) is not None
                and other_value.raw != value.raw
                and ev.unit_rank_score(other, plan) >= best_score - _AMBIGUITY_MARGIN
            ]
            alternates_same_doc = [
                other for other in competitors if other.document_id == best.document_id
            ]
            if alternates_same_doc:
                names = ", ".join(
                    sorted(
                        {
                            other.requested_value(plan).raw
                            for other in alternates_same_doc
                            if other.requested_value(plan)
                        }
                    )
                )
                abstain = (
                    f"{_NO_EVIDENCE_TEXT} on {best.document_name}, page "
                    f"{best.page_number}: the OCR reads {value.raw} next to "
                    f"{_value_line(best, plan)!r}, and {names} in equally matching "
                    "text on the same page. Re-check that page before relying on "
                    "either figure."
                )
                return None, [best, *alternates_same_doc[:2]], abstain

            other_docs = [
                other
                for other in competitors
                if other.document_id != best.document_id and other.matched_terms
            ][:3]
            context = _value_line(best, plan)
            answer = (
                f"{context}: {value.display} — {best.document_name}, page {best.page_number}."
                if context
                else f"{value.display} — {best.document_name}, page {best.page_number}."
            )
            used = [best]
            if other_docs:
                extras = "; ".join(
                    f"{other.requested_value(plan).display} in {other.document_name}, "
                    f"page {other.page_number}"
                    for other in other_docs
                    if other.requested_value(plan)
                )
                if extras:
                    answer += f"\n(Other documents in scope read: {extras}.)"
                    used.extend(other_docs)
            if value.state != "verified":
                answer += (
                    "\nThis reading is not verified: the characters above are quoted "
                    "exactly as the OCR produced them."
                )
            return answer, used[:_ANSWER_UNIT_LIMIT], None

    if plan.table_intent:
        headers = ev.table_header_units(units)
        if headers:
            lines = []
            for unit in headers[:6]:
                columns = [cell for cell in unit.header if cell] or [
                    cell for cell in unit.text.split("|") if cell.strip()
                ]
                lines.append(
                    "- "
                    + " | ".join(cell.strip() for cell in columns[:8])
                    + f" — {unit.document_name}, page {unit.page_number}"
                )
            used_headers = headers[:6]
            rows = [unit for unit in units if unit.is_table_row and unit not in used_headers][:4]
            for unit in rows:
                lines.append(
                    f"- row: {unit.citation_snippet(160)} — "
                    f"{unit.document_name}, page {unit.page_number}"
                )
            return (
                "Table headings recovered from the authorized documents:\n" + "\n".join(lines),
                [*used_headers, *rows][:_ANSWER_UNIT_LIMIT],
                None,
            )

    if plan.aggregate:
        items = ev.list_item_units(units, limit=12)
        if items:
            lines = [
                f"- {unit.citation_snippet(160)} — "
                f"{unit.document_name}, page {unit.page_number}"
                for unit in items
            ]
            return (
                "Entries found in the authorized documents:\n" + "\n".join(lines),
                items[:_ANSWER_UNIT_LIMIT],
                None,
            )

    if plan.yes_no:
        hit = next((unit for unit in units if unit.matched_terms), None)
        if hit is not None:
            answer = (
                f"Yes — {hit.document_name}, page {hit.page_number} reads "
                f"{hit.citation_snippet(200)!r}."
            )
            return answer, [hit], None

    top = units[0]
    if top.matched_terms or top.semantic_raw >= EVIDENCE_THRESHOLD:
        answer = (
            f"{top.document_name}, page {top.page_number}: "
            f"{top.citation_snippet(240)!r}"
        )
        return answer, [top], None

    return None, [], None


def _abstain(
    question: str,
    units: list[Any],
    diagnostics: dict[str, Any],
    notes: str,
) -> ChatQueryResponse:
    """Say that nothing supports the answer, and name what was checked."""
    if units:
        pages = "; ".join(
            f"{unit.document_name}, page {unit.page_number}" for unit in units[:3]
        )
        answer = (
            f"{_NO_EVIDENCE_TEXT} The closest passages on {pages} do not state it, "
            "so no value is being guessed."
        )
    else:
        scanned = diagnostics.get("pages_scanned", 0)
        scope = diagnostics.get("effective_scope") or []
        where = f" in {len(scope)} selected document(s)" if scope else ""
        answer = (
            f"{_NO_EVIDENCE_TEXT} {scanned} page(s) of authorized OCR text{where} "
            "were checked and none carries the wording or value this question asks for."
        )
    return ChatQueryResponse(
        answer=answer,
        sources=[],
        confidence=0.0,
        question=question,
        has_evidence=False,
        engine="no_evidence",
        notes=notes,
    )


def _clean_history(raw: Any) -> list[dict[str, str]]:
    """Keep only the speaker turns a follow-up can be read against.

    Anything the client labels as a system message, and any empty turn, is
    dropped rather than forwarded to the model, because history is untrusted
    input and the retrieval scope never widens because of it.
    """
    turns: list[dict[str, str]] = []
    for message in raw or []:
        role = str(getattr(message, "role", None) or (message.get("role") if isinstance(message, dict) else ""))
        content = str(
            getattr(message, "content", None)
            or (message.get("content") if isinstance(message, dict) else "")
        ).strip()
        if content and role in ("user", "assistant"):
            turns.append({"role": role, "content": content})
    return turns


def _last_user_turn(history: list[dict[str, str]]) -> str:
    for message in reversed(history):
        if message["role"] == "user":
            return message["content"]
    return ""


def _try_local_llm(
    question: str,
    plan: Any,
    units: list[Any],
    history: list[dict[str, str]] | None = None,
) -> Optional[ChatQueryResponse]:
    """Ground one answer in the local Ollama model, or return None.

    The model only ever sees units that already passed the evidence gate, and its
    answer is kept only if it quotes them verbatim and every substantial number
    appears in the passages. Anything else falls through to the deterministic
    answer, and the engine field always says which path produced the text.
    Conversation history is offered to the model as wording context only: it is
    never retrieved over, and the numeric evidence check still requires every
    substantial figure to occur in the passages above.
    """
    from app.services import llm_service

    if not units or not llm_service.is_usable():
        return None

    blocks = [unit.evidence_block(index) for index, unit in enumerate(units[:6], 1)]
    result = llm_service.generate_answer(question, blocks, history)
    if result is None:
        return None

    cited = llm_service.verify_quotes(result.text, blocks)
    if not cited:
        logger.info("Local model answered without quoting the evidence; discarding")
        return None

    used_units = [units[index - 1] for index in cited if 1 <= index <= len(units)]
    evidence_chunks = [{"snippet": unit.text} for unit in units[:6]]
    if not _numbers_supported_by_evidence(result.text, evidence_chunks):
        logger.info("Local model produced numbers absent from the evidence; discarding")
        return None
    if not _names_supported_by_evidence(result.text, units[:6], question):
        logger.info("Local model produced a name absent from the evidence; discarding")
        return None

    return ChatQueryResponse(
        answer=result.text,
        sources=_citations(used_units or units[:3], plan),
        confidence=_evidence_confidence(plan, used_units or units),
        question=question,
        has_evidence=True,
        engine="llm_ollama",
        notes=(
            f"Generated locally by {result.model} from the cited passages only; "
            "no document content left this machine."
        ),
    )


def _answer_single_part(
    db: Session,
    question: str,
    user: Any | None,
    allowed_ids: list[int],
    history: list[dict[str, str]] | None = None,
) -> ChatQueryResponse:
    """Retrieve, generate and package the answer for one single-part question."""
    from app.services import evidence_service as ev

    history = history or []
    plan = ev.build_query_plan(question)
    anchored = [] if allowed_ids else ev.resolve_document_scope(db, question, user)
    units, diagnostics = ev.collect_evidence(
        db,
        plan,
        user=user,
        document_ids=allowed_ids or None,
        anchored_ids=anchored,
        max_units=24,
    )

    # An elliptical follow-up ("and the due date?") carries no wording of its own
    # to retrieve over. Retry once with the earlier question's phrasing added; the
    # evidence gate is untouched, so this can only widen recall, never certainty.
    carried_from = ""
    if not units:
        prior = _last_user_turn(history)
        if prior and prior.lower() != question.lower():
            carry_plan = ev.build_query_plan(f"{prior} {question}")
            carry_units, diagnostics = ev.collect_evidence(
                db,
                carry_plan,
                user=user,
                document_ids=allowed_ids or None,
                anchored_ids=anchored,
                max_units=24,
            )
            if carry_units:
                plan, units, carried_from = carry_plan, carry_units, prior

    carry_note = (
        " Retrieval also used the wording of your earlier question "
        f"({carried_from[:80]!r}) because this follow-up named nothing on its own."
        if carried_from
        else ""
    )

    # A question about what a document is about quotes nothing specific inside it,
    # so its own wording can only ever match a line by coincidence ("the line that
    # happens to contain the word 'subject'). When the document is already chosen,
    # its opening lines lead the pool instead: same selected ids, no wider
    # retrieval, and no threshold moved.
    front_matter = ""
    if _is_overview_question(question):
        target_ids = allowed_ids or (anchored if anchored and len(anchored) <= 4 else [])
        if target_ids:
            opening = ev.front_matter_units(db, target_ids, user=user)
            diagnostics["front_matter_ids"] = [int(value) for value in target_ids]
            if opening:
                units = [*opening, *units]
                front_matter = ", ".join(unit.document_name for unit in opening)

    front_note = (
        f" Quoted from the opening lines of {front_matter}, because this question "
        "asks what the document is about instead of for a value written in it."
        if front_matter
        else ""
    )
    context_note = carry_note + front_note

    answer, used, abstention = _compose_deterministic(plan, units)

    # Amounts, dates, identifiers, tables and lists stay deterministic. Free-form
    # questions get one grounded pass through the local model, which is what lets
    # a paraphrase ("which activity involves reading?") reach the right line.
    llm_response: Optional[ChatQueryResponse] = None
    llm_attempted = False
    if plan.value_kind is None and not plan.table_intent and not plan.aggregate:
        llm_attempted = True
        llm_response = _try_local_llm(question, plan, units, history)
        if llm_response is not None:
            if context_note:
                llm_response.notes = (llm_response.notes or "") + context_note
            return llm_response

    if abstention is not None:
        return ChatQueryResponse(
            answer=abstention,
            sources=_citations(used, plan, limit=3),
            confidence=0.0,
            question=question,
            has_evidence=False,
            engine="ambiguous_evidence",
            notes=(
                "Two readings of the requested value sit in the same document, so "
                "the source does not support choosing one."
            ),
        )

    if answer is not None and used:
        return ChatQueryResponse(
            answer=answer,
            sources=_citations(used, plan),
            confidence=_evidence_confidence(plan, used),
            question=question,
            has_evidence=True,
            engine="extractive_evidence",
            notes=(
                "Built from the cited OCR lines only. Values marked as damaged are "
                "quoted as the recognizer produced them." + context_note
            ),
        )

    # Reuse the attempt above when there was one: the model, the passages and the
    # gate are identical, so a rejected answer must not cost a second inference.
    if not llm_attempted:
        llm_response = _try_local_llm(question, plan, units, history)
    if llm_response is not None:
        if context_note:
            llm_response.notes = (llm_response.notes or "") + context_note
        return llm_response

    if front_matter and units:
        # The model is not available or rejected its own answer, so quote the
        # front of the document instead: same scope, same OCR text, no inference.
        top = next(unit for unit in units if unit.source_kind == "front_matter")
        return ChatQueryResponse(
            answer=(
                f"{top.document_name}, page {top.page_number} opens with: "
                f"{top.citation_snippet(300)!r}"
            ),
            sources=_citations(units[:3], plan),
            confidence=_evidence_confidence(plan, units),
            question=question,
            has_evidence=True,
            engine="extractive_evidence",
            notes=(
                "The opening lines of the selected document are quoted verbatim; "
                "nothing was retrieved from outside it." + context_note
            ),
        )

    return _abstain(
        question,
        units,
        diagnostics,
        (
            "Evidence gate: a page must clear the calibrated raw similarity floor, "
            "or match the question's wording inside one line, row or label-value window."
        ),
    )


def _answer_multipart(
    db: Session,
    question: str,
    parts: list[str],
    user: Any | None,
    allowed_ids: list[int],
    history: list[dict[str, str]] | None = None,
) -> ChatQueryResponse:
    """Answer each clause of a compound question from its own evidence."""
    responses = [
        _answer_single_part(db, part, user, allowed_ids, history) for part in parts
    ]
    answered = [
        (part, response) for part, response in zip(parts, responses) if response.has_evidence
    ]
    if not answered:
        merged = responses[0]
        return ChatQueryResponse(
            answer=merged.answer,
            sources=[],
            confidence=0.0,
            question=question,
            has_evidence=False,
            engine=merged.engine,
            notes=merged.notes,
        )

    lines = ["Based on the retrieved authorized documents:"]
    for part, response in answered:
        lines.append(f"- {part}: {response.answer}")
    missing = [part for part, response in zip(parts, responses) if not response.has_evidence]
    if missing:
        lines.append(
            "\n*(Not supported by the authorized documents: " + "; ".join(missing) + ".)*"
        )

    sources: list[SourceCitation] = []
    seen_keys: set[tuple[int, int, str]] = set()
    for _, response in answered:
        for citation in response.sources:
            key = (citation.document_id, citation.page_number or 0, citation.snippet)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sources.append(citation)
    engines = sorted({response.engine for _, response in answered})
    return ChatQueryResponse(
        answer="\n".join(lines),
        sources=sources[:6],
        confidence=min(response.confidence for _, response in answered),
        question=question,
        has_evidence=not missing,
        engine=engines[0] if len(engines) == 1 else "+".join(engines),
        notes=(
            "Each clause was answered from its own cited evidence; a clause without "
            "support is listed instead of guessed."
        ),
    )


def answer_question(
    db: Session,
    request: ChatQueryRequest,
    user: Any | None = None,
) -> ChatQueryResponse:
    """Answer from authorized OCR evidence: hybrid retrieval, then generation."""
    from app.services import evidence_service as ev

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

    requested_ids = list(request.document_ids or [])
    if request.document_id is not None:
        requested_ids.append(int(request.document_id))

    allowed, rejected = ev.scope_document_ids(db, user, requested_ids or None)
    if requested_ids and rejected:
        return ChatQueryResponse(
            answer=(
                "Access to one or more requested documents is not authorized for your department. "
                "Cross-department document access is forbidden."
            ),
            sources=[],
            confidence=0.0,
            question=clean_q,
            has_evidence=False,
            engine="unauthorized",
        )
    if requested_ids and not allowed:
        return ChatQueryResponse(
            answer=(
                "Access to the requested document is not authorized, or it has no "
                "usable OCR text yet. Nothing was retrieved."
            ),
            sources=[],
            confidence=0.0,
            question=clean_q,
            has_evidence=False,
            engine="unauthorized",
        )

    # The catalog answer reads the document table, not the evidence pool, so it
    # must run after the scope exists: a selected document turns a library listing
    # into a listing of the selection, and an unselected name must not appear.
    if _is_application_metadata_question(clean_q):
        return _answer_application_metadata_question(db, clean_q, user, allowed)

    parts = _split_multipart_question(clean_q)
    history = _clean_history(request.history)
    if len(parts) > 1:
        return _enforce_scope(
            _answer_multipart(db, clean_q, parts, user, allowed, history), allowed
        )

    return _enforce_scope(
        _answer_single_part(db, clean_q, user, allowed, history), allowed
    )


def _enforce_scope(
    response: ChatQueryResponse, allowed_ids: Sequence[int]
) -> ChatQueryResponse:
    """Drop citations naming a document the caller did not select.

    Retrieval, reranking and evidence assembly all take the scope, so this is the
    last check before an answer leaves the service: a source card the user cannot
    open, or that names a document the "+" selector excluded, is a correctness
    failure rather than a ranking one.
    """
    if not allowed_ids or not response.sources:
        return response
    allowed = {int(value) for value in allowed_ids}
    kept = [source for source in response.sources if source.document_id in allowed]
    if len(kept) == len(response.sources):
        return response
    logger.warning(
        "Dropped %d out-of-scope citation(s) from a %s answer",
        len(response.sources) - len(kept),
        response.engine,
    )
    return response.model_copy(update={"sources": kept})


def _numbers_supported_by_evidence(answer: str, chunks: list[dict[str, Any]]) -> bool:
    """True when every substantial number in the answer is anchored in the evidence.

    Comparison ignores thousands separators (1,250 vs 1250) and Devanagari digits.
    Anchoring is local, not global: a page that holds both "MH" and "198", on
    different lines, does not support "MH 198". A model that welds a vehicle
    prefix to a bag count has invented a figure the source never joined, so each
    number must share an evidence fragment with the document word the answer
    places beside it. Answers without substantial numbers are unconstrained.
    """
    from app.services import evidence_service as ev

    snippets = [str(chunk.get("snippet") or "") for chunk in chunks]
    deva_digits = str.maketrans("०१२३४५६७८९", "0123456789")

    def fold(text: str) -> str:
        return text.replace(",", "").translate(deva_digits).lower()

    whole = fold(" ".join(snippets))
    fragments = [
        fold(fragment)
        for snippet in snippets
        for fragment in re.split(r"[\n|/]+|\s{2,}", snippet)
        if fragment.strip()
    ]

    for match in re.finditer(r"\d[\d,.]{2,}", answer or ""):
        compact = match.group(0).strip(".,").replace(",", "")
        if compact not in whole:
            return False
        neighbours = _number_context_words(answer, match.start(), match.end())
        # Only a word the model borrowed from the document can bind a figure to
        # its context; "rupees" added in the model's own vocabulary proves nothing.
        borrowed = tuple(word for word in neighbours if word in whole)
        if borrowed and not any(
            compact in fragment and any(word in fragment for word in borrowed)
            for fragment in fragments
        ):
            return False
    return True


def _number_context_words(answer: str, start: int, end: int) -> tuple[str, ...]:
    """The words written immediately beside a number in the answer."""
    from app.services import evidence_service as ev

    before = re.findall(r"[A-Za-zऀ-ॿ]+", answer[max(0, start - 24):start])
    after = re.findall(r"[A-Za-zऀ-ॿ]+", answer[end:end + 24])
    picks = ([before[-1]] if before else []) + ([after[0]] if after else [])
    words: list[str] = []
    for raw in picks:
        lowered = raw.lower()
        # Short all-caps reference prefixes ("MH", "SD") are exactly the
        # associations worth checking, so capitals earn a two-letter word a place.
        if len(lowered) < 3 and not (raw.isupper() and len(raw) >= 2):
            continue
        if lowered in ev.QUERY_STOPWORDS:
            continue
        words.append(lowered)
    return tuple(dict.fromkeys(words))


# Two consecutive capitalized words is how a name gets written, and the run is
# what makes it checkable: a lone initial capital is usually just a sentence start.
_PROPER_NOUN_RUN_RE = re.compile(
    r"[A-Z][A-Za-z0-9'’-]*(?:\s+[A-Z][A-Za-z0-9'’-]*)+"
)
# A filename in an answer is a claim about which document the answer came from.
_FILE_NAME_RE = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9 ._#()\[\]-]*?\.(?:pdf|png|jpe?g|tif+f|bmp|docx?|xlsx?|pptx?|csv|txt)",
    re.IGNORECASE,
)
# Words that only join a name together, so they carry no evidence of their own.
_NAME_CONNECTORS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at", "by",
    "this", "that", "these", "those", "it", "its", "is", "are", "was", "were",
}


def _names_supported_by_evidence(
    answer: str, units: list[Any], question: str
) -> bool:
    """True when the answer names nothing the passages lack.

    The units handed to the model are already scoped, but a local model can still
    fill a gap from its own memory or from an earlier turn, and an invented name
    reads exactly like a grounded one. Two claims are checked: a filename in the
    answer has to be one of the documents actually retrieved, and every run of
    consecutive capitalized words has to be attested in a passage, a document name
    or the question itself. A run passes when the whole phrase occurs, or when each
    substantial word does, because OCR splits names across lines.
    """
    from app.services import evidence_service as ev

    ground = re.sub(
        r"\s+",
        " ",
        ev.normalize_text(
            " \n".join(
                [unit.text for unit in units]
                + [unit.document_name for unit in units]
                + [question or ""]
            )
        ).lower(),
    )
    text = ev.normalize_text(answer or "")

    for match in _FILE_NAME_RE.finditer(text):
        named = match.group(0).strip().lower()
        if not any(named == unit.document_name.lower() for unit in units):
            return False

    for match in _PROPER_NOUN_RUN_RE.finditer(text):
        words = match.group(0).split()
        if " ".join(words).lower() in ground:
            continue
        substantial = [
            word.lower()
            for word in words
            if word.lower() not in _NAME_CONNECTORS and len(word) > 2
        ]
        if not substantial:
            continue
        if not all(word in ground for word in substantial):
            return False
    return True

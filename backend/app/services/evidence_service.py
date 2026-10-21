"""Hybrid evidence assembly for retrieval and retrieval-augmented answering.

Retrieval used to hand whole page chunks to the answer extractor, which then had
to guess which OCR line belonged to which value. This module keeps the page's
reading order intact: evidence units are OCR line windows, table rows and single
lines, each carrying the amount/date/identifier tokens that sit *inside* it, so a
label and its value are only ever associated when the source text puts them in
the same window.

Three independent support signals exist and none of them boosts another:
  * ``semantic_raw``  - raw cosine similarity from the vector store, gated by
    ``settings.SEMANTIC_MIN_SCORE`` (never lowered here).
  * ``coverage``      - deterministic lexical/fuzzy term coverage inside one window.
  * ``anchored``      - the user named the document in the question, so evidence
    must come from that document rather than from the best global similarity.
Ranking may combine them; answerability may not be reached by a ranking boost.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\u0900-\u097F\uA8E0-\uA8FF]+", re.UNICODE)
_DEVANAGARI_DIGIT_MAP = str.maketrans("०१२३४५६७८९", "0123456789")
_CURRENCY_GLYPHS = "$€£₹"

# Minimum raw cosine similarity before semantic similarity alone can support an
# answer. Read from the calibrated setting; never lowered per-query.
SEMANTIC_SUPPORT_FLOOR = settings.SEMANTIC_MIN_SCORE

# Minimum term coverage for a window to be usable as evidence.
LEXICAL_RANK_FLOOR = 0.20

# A question that names more documents than this is not really naming a scope.
_MAX_ANCHORED_SCOPE = 8

QUERY_STOPWORDS = {
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "can", "could", "should",
    "would", "shall", "will", "may", "might", "must", "and", "or", "but",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "up",
    "about", "into", "over", "after", "it", "its", "they", "them", "their",
    "this", "that", "these", "those", "tell", "show", "give", "please",
    "find", "list", "name", "mentioned", "mention", "document", "documents",
    "file", "files", "page", "pages", "record", "records", "print",
    "printed", "available", "authorized", "question", "there", "them",
    "own", "your", "my", "our", "all", "any", "each", "every", "both",
    "across", "between", "compare", "compared", "versus", "than", "more",
    "less", "most", "much", "many", "several", "contain", "contains",
    "containing", "present", "presents", "existing", "know", "else",
    "एक", "दोन", "सगळ", "कोणता", "कोणते", "किती", "ज्या", "जे", "जी",
    "क्या", "कौन", "किस", "काय", "कोण",
    "कुठे", "केव्हा", "आहे", "आह", "या", "चा", "ची", "चे", "of",
}

# Words that describe the artefact rather than its content. They still count as
# terms, but with a lower weight so they cannot carry an answer alone.
STRUCTURAL_WORDS = {
    "table", "tables", "column", "columns", "heading", "headings", "row",
    "rows", "text", "content", "section", "entry", "entries", "first",
    "second", "third", "main", "primary", "listed", "lists", "list",
    "activity", "activities", "purpose", "purposes", "details", "detail",
    "information", "data", "summary", "value", "values", "line", "lines",
    "item", "items", "one", "two", "other", "others", "different",
    "involves", "involve", "involving", "related", "found", "find",
    "पयम", "माहोती", "बाबत",
}

# Plural nouns that ask for a set of entries rather than for one match. They
# make a question a collection request only when the question is not already
# asking for a single value of that kind.
_LIST_NOUNS = {
    "activities", "items", "entries", "rows", "dates", "numbers", "amounts",
    "values", "purposes", "details", "names", "charges", "fees", "clauses",
    "headings", "columns", "sections", "parts", "steps", "totals",
    "मदी", "दिनांक", "नाम", "राशी",
}

_AMOUNT_RE = re.compile(
    r"[$€£₹]\s?\d[\d,.]*|\d[\d,.]*\s?(?:Rs\.?|INR|USD|EUR|GBP|dollars?|rupees?|cents?)\b",
    re.IGNORECASE,
)
_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
_DATE_RE = re.compile(
    r"\b\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}\b|"
    r"\b(?:" + "|".join(name.capitalize() for name in _MONTH_NAMES) + r")"
    r"\s+\d{1,2},?\s+\d{4}\b|"
    r"\b\d{1,2}\s+(?:" + "|".join(name.capitalize() for name in _MONTH_NAMES) + r")\s+\d{4}\b|"
    r"(?:दिनांक|तारीख|माहोती|दिवस)\s*[:#\-]?\s*\d{1,2}[/.\-]\d{1,2}[/.\-]\d{1,4}",
    re.IGNORECASE,
)
_IDENTIFIER_RE = re.compile(
    r"\b[A-Za-z]{2,}[-_/]\s?#?\s?\d[\w./-]*"
    r"|\b\d[\w-]*[-_][A-Za-z]{2,}[\w-]*"
    r"|\b(?:no|nos|num|number|ref|reference|id|inv|invoice|acc|account|challan|sl|cr)\.?\s*[:#-]?\s*\d[\w./-]{1,20}",
    re.IGNORECASE,
)
# Currency vocabulary, not document-specific: decides whether a bare digit run on
# a line can be read as money at all.
_AMOUNT_HINT_RE = re.compile(
    r"(rs\.?|inr|rupee|dollar|euro|₹|रु|रुपये|राशि|रकम|amount|total|sum|किंमत|देयक|एकूण)",
    re.IGNORECASE,
)
_CURRENCY_WORD_RE = re.compile(
    r"(rs\.?|inr|usd|eur|gbp|rupee|dollar|euro|₹)", re.IGNORECASE
)
# One decimal amount with an optional thousands grouping: 1250.00, 24555.80,
# 3,000.45.  The unseparated form has to count: most Indian invoices write the
# total without commas, and reading "24555.80" as "not money" left a unit rate as
# the only amount on the page.
_MONEY_FORM_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{1,2}$")

# A recognised thousands group left dangling at the end of a line, and the decimal
# remainder that starts the next one.  Page-3 of a Marathi bill reads "25," then
# "145.00" because the recogniser put the comma break on its own line; publishing
# "145.00" as that page's amount would quote a figure the page never states alone.
_DANGLING_THOUSANDS_RE = re.compile(r"(\d{1,3})\s*,\s*$")
_AMOUNT_HEAD_RE = re.compile(r"^\s*(\d{1,3}\.\d{1,2})\b")

# Filename tokens that describe a container or a dump artefact, never content.
_GENERIC_FILE_TOKENS = {
    "file", "files", "page", "pages", "pdf", "png", "jpg", "jpeg", "docx",
    "doc", "scan", "scanned", "copy", "original", "untitled", "new", "test",
    "final", "draft", "sample", "image", "photo", "document", "documents",
}

# Question-intent vocabularies (English, Hindi, Marathi) - generic, not
# document-specific, and used only to choose which value type to look for.
_MONEY_HINTS = (
    "cost", "price", "amount", "total", "sum", "fee", "charge", "worth", "paid",
    "pay", "owe", "balance", "how much", "how many rupees",
    "कितना", "कितनी", "कितने", "राशि", "रकम", "मूल्य", "लागत", "कुल", "जमा",
    "देय", "बकाया", "पैसे", "दाम", "किती", "एकूण", "देयक", "किंमत", "दर",
)
_DATE_HINTS = (
    "date", "when", "deadline", "due", "period", "valid", "expiry", "submitted",
    "दिनांक", "तारीख", "कब", "समय", "अवधि", "केव्हा", "वेळ", "मुदत", "शेवटची",
)
# Words that can only open a request for a time. "due" and "valid" sit in
# _DATE_HINTS as context nouns; these are the interrogatives themselves.
_WHEN_INTERROGATIVES = ("when", "कब", "केव्हा")
_IDENTIFIER_HINTS = (
    # Only words that ask for a designation. Nouns that name the artifact itself
    # ("invoice", "challan", "bill") are left out: "what is the total due on the
    # invoice" wants an amount, and an artifact noun must not out-rank the value
    # noun the question actually uses.
    "number", "reference", "ref", "id",
    "order no", "code", "account", "sl no", "sl.", "cr no",
    "संख्या", "क्रमांक", "खाता", "ओळख", "नंबर", "खाते",
)
_AGGREGATION_HINTS = (
    "all", "every", "each", "across", "both", "compare", "difference",
    "multiple", "how many", "give me all", "enumerat",
    "सर्व", "प्रत्येक", "सभी", "हर", "कोणकोणते", "यादी", "तुलना", "सगळी", "सगळे",
)
_TABLE_HINTS = ("table", "column", "columns", "heading", "headings", "row", "rows",
                "तालिका", "स्तंभ", "मद", "पंक्ती")


@dataclass(frozen=True)
class ValueToken:
    """A money/date/identifier token recovered from OCR text.

    ``state`` distinguishes a trustworthy reading from a damaged one so callers
    can never present a corrupted digit run as a verified number:
      verified  - the token parses cleanly in the source script.
      corrupted - digits are recoverable but surrounding glyphs are damaged.
      unknown   - no trustworthy numeric reading exists.
    """

    raw: str
    kind: str
    state: str
    normalized: str | None = None
    artifacts: tuple[str, ...] = ()

    @property
    def display(self) -> str:
        if self.state == "verified":
            return self.raw
        if self.state == "corrupted" and self.normalized:
            return f"{self.raw} (OCR damaged; digits read as {self.normalized})"
        return f"{self.raw} (not readable as a value)"


@dataclass
class QueryPlan:
    """Deterministic interpretation of a question, shared by retrieval and answering."""

    question: str
    terms: tuple[str, ...] = ()
    weighted_terms: tuple[tuple[str, float], ...] = ()
    phrases: tuple[str, ...] = ()
    value_kind: str | None = None
    aggregate: bool = False
    yes_no: bool = False
    table_intent: bool = False
    numeric_literals: tuple[str, ...] = ()


@dataclass
class EvidenceUnit:
    """One OCR line window, table row or chunk that can support an answer."""

    document_id: int
    document_name: str
    page_number: int
    chunk_index: int
    text: str
    lines: tuple[str, ...] = ()
    values: tuple[ValueToken, ...] = ()
    header: tuple[str, ...] = ()
    is_table_row: bool = False
    semantic_raw: float = 0.0
    coverage: float = 0.0
    strength: float = 0.0
    anchored: bool = False
    matched_terms: tuple[str, ...] = ()
    specific_matches: tuple[str, ...] = ()
    fuzzy_terms: tuple[tuple[str, str, float], ...] = ()
    source_kind: str = "line_window"
    relaxed: bool = False
    line_index: int = -1

    @property
    def value_tokens(self) -> tuple[str, ...]:
        return tuple(value.raw for value in self.values)

    def requested_values(self, plan: QueryPlan) -> tuple[ValueToken, ...]:
        """Readings of the kind this question asks for, inside this window.

        A whole-page chunk is excluded: its lines carry several labels and values,
        so quoting a value from it would associate the value with whatever the
        chunk's first line happens to say.
        """
        if not plan.value_kind or self.source_kind == "chunk":
            return ()
        return tuple(
            value
            for value in self.values
            if value.kind == plan.value_kind and value.state != "unknown"
        )

    def requested_verified(self, plan: QueryPlan) -> tuple[ValueToken, ...]:
        """Readings of the requested kind that survive validation cleanly."""
        if not plan.value_kind:
            return ()
        return tuple(
            value
            for value in self.requested_values(plan)
            if value.state == "verified"
        )

    def requested_value(self, plan: QueryPlan) -> ValueToken | None:
        """The window's value, only when the window supports exactly one reading.

        Two verified candidates of the same kind in one window means the
        association is not supported by the source text, so the extractor must
        not pick one of them. Damaged readings alongside a single clean one are
        kept - they are reported as uncertainty, not silently dropped.
        """
        verified = self.requested_verified(plan)
        if len(verified) == 1:
            return verified[0]
        if not verified:
            values = self.requested_values(plan)
            if len(values) == 1:
                return values[0]
        return None

    def looks_like_list_item(self) -> bool:
        """A window that reads as one entry: a label plus exactly one amount.

        Requiring the amount is what keeps an entry apart from a stray OCR line
        ("YOu?"), and allowing only one keeps a label from being charged two
        different values.
        """
        if self.source_kind == "chunk" or not self.lines or len(self.lines) > 5:
            return False
        amounts = [value for value in self.values if value.kind == "amount"]
        if len(amounts) != 1:
            return False
        labels = [
            token for token in tokenize(self.text) if len(token) >= 3 and not token.isdigit()
        ]
        return len(labels) >= 2

    def semantic_supported(self) -> bool:
        return self.semantic_raw >= SEMANTIC_SUPPORT_FLOOR

    def lexically_supported(self, plan: QueryPlan) -> bool:
        """True when the window itself proves the match, without any semantic help.

        Normally a rare-in-corpus term matched exactly, or two term matches, is
        required. When the question named this document, one window match is
        enough - the user already told us where to look, and unrelated documents
        cannot enter on that signal. Either way a value question needs the value
        inside the same window.
        """
        floor = LEXICAL_RANK_FLOOR if not self.anchored else 0.12
        if not self.matched_terms or self.coverage < floor:
            # Inside a named document, one clean value of the requested kind plus
            # any label match is evidence; coverage maths alone can hide a value
            # behind a damaged label ("TOTA" for TOTAL).
            if not (
                self.anchored
                and plan.value_kind
                and self.matched_terms
                and len(self.requested_verified(plan)) == 1
            ):
                return False
        if not self.anchored:
            strong = bool(self.specific_matches)
            confirmed = len(self.matched_terms) >= 2
            if not (strong or confirmed):
                return False
        if plan.value_kind:
            if not self.requested_values(plan):
                return False
            if len(self.requested_verified(plan)) > 1:
                # Two clean candidate values in one window: the source does not
                # support picking one, so this window cannot carry the answer.
                return False
        return True

    def structurally_supported(self, plan: QueryPlan) -> bool:
        """Support for questions whose evidence is structure, not wording.

        "List every date across my documents", "what are the column headings" and
        "which activities are listed" ask for values or rows, not for a term
        match, so the requested kind itself is the evidence. Chunks are excluded:
        an answer must point at one line or row. Only ever applies inside the
        retrieved scope, and value states keep damaged readings from being
        reported as verified.
        """
        if self.source_kind == "chunk":
            return False
        if plan.table_intent and (self.is_table_row or bool(self.header)):
            return True
        if not plan.aggregate:
            return False
        if plan.value_kind and self.requested_values(plan):
            return True
        return (not plan.value_kind) and (self.is_table_row or self.looks_like_list_item())

    def is_grounded(self, plan: QueryPlan) -> bool:
        return (
            self.semantic_supported()
            or self.lexically_supported(plan)
            or self.structurally_supported(plan)
        )

    def support_reason(self, plan: QueryPlan) -> str:
        if self.source_kind == "front_matter":
            return "front_matter"
        if self.semantic_supported():
            return "semantic"
        if self.lexically_supported(plan):
            if self.anchored and self.relaxed:
                return "anchored_fuzzy"
            return "anchored_lexical" if self.anchored else "lexical"
        if self.structurally_supported(plan):
            if self.is_table_row or self.header:
                return "table"
            return "aggregate"
        return "none"

    def citation_snippet(self, max_chars: int = 250) -> str:
        prefix = ""
        if self.header:
            prefix = "[table columns: " + " | ".join(c for c in self.header if c) + "] "
        body = " / ".join(line for line in self.lines if line) or self.text.replace("\n", " / ")
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "…"
        return prefix + body

    def evidence_block(self, index: int) -> str:
        header = (
            f"[{index}] document_id={self.document_id} document={self.document_name} "
            f"page={self.page_number} source={self.source_kind}"
        )
        if self.header:
            header += " table_columns=" + " | ".join(cell for cell in self.header if cell)
        if self.values:
            header += " values_in_window=" + ", ".join(
                f"{value.raw}({value.kind}/{value.state})" for value in self.values[:6]
            )
        return f"{header}\n<untrusted_document_text>\n{self.text}\n</untrusted_document_text>"


def normalize_text(text: str) -> str:
    """NFKC-normalize for matching; callers keep the original text for display."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")


def normalize_token(token: str) -> str:
    return normalize_text(token).strip().lower()


def tokenize(text: str) -> list[str]:
    return [normalize_token(match.group(0)) for match in _TOKEN_RE.finditer(text or "")]


_SEPARATORS_RE = re.compile(r"[\s_.\-/\\]+")


def join_separators(text: str) -> str:
    """The text with every word separator removed.

    "File 04", "file_04" and "file04" are three spellings of one reference to a
    file, and OCR adds spaces, underscores and dashes interchangeably.
    """
    return _SEPARATORS_RE.sub("", normalize_text(text or "").lower())


def glued_tokens(text: str) -> list[str]:
    """Runs joined by -_./ so a reference number stays one matchable token.

    tokenize() splits "INV-3337" into "inv" and "3337", which throws away the
    digit sequence an identifier comparison needs.
    """
    return [
        normalize_token(match.group(0))
        for match in re.finditer(r"\w+(?:[-_./]\w+)+", normalize_text(text or ""))
        if len(match.group(0)) >= 4
    ]


def _has_digit(text: str) -> bool:
    return any(character.isdigit() for character in text)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", normalize_text(text).translate(_DEVANAGARI_DIGIT_MAP))


def _digit_only(text: str) -> bool:
    stripped = re.sub(r"[,\s.]", "", text).translate(_DEVANAGARI_DIGIT_MAP)
    stripped = re.sub(r"[%$€£₹/]", "", stripped)
    return bool(stripped) and stripped.isdigit()


def classify_value(raw: str, kind: str | None = None) -> ValueToken | None:
    """Classify one candidate value token, never silently repairing damaged text."""
    token = normalize_text(raw).strip()
    if not token or not _has_digit(token):
        return None

    digit_run = _digits(token)
    if len(digit_run) < 2 and kind not in ("date", "identifier"):
        # A single digit is almost always OCR noise, not a document value.
        return None

    ascii_digits = [character for character in token if character.isdigit() and character.isascii()]
    deva_digits = [character for character in token if "०" <= character <= "९"]
    mixed_scripts = bool(ascii_digits) and bool(deva_digits)

    letters = [character for character in token if character.isalpha()]
    latin_letters = [character for character in letters if character.isascii()]
    deva_digit_letters = [ch for ch in letters if "\u0966" <= ch <= "\u096F"]
    other_letters = [
        ch for ch in letters
        if not ch.isascii() and not ("\u0966" <= ch <= "\u096F")
    ]
    symbol_count = sum(token.count(symbol) for symbol in _CURRENCY_GLYPHS)

    resolved = kind
    if resolved is None:
        if _DATE_RE.fullmatch(token):
            resolved = "date"
        elif symbol_count or re.search(r"(rs|inr|usd|eur|gbp|rupees|dollars)", token, re.I):
            resolved = "amount"
        elif _IDENTIFIER_RE.fullmatch(token):
            resolved = "identifier"
        else:
            resolved = "number"

    if resolved == "amount" and symbol_count == 0 and not _CURRENCY_WORD_RE.search(token):
        # Promoted from a bare decimal cell: only money-shaped readings count.
        # "2118." and "5..900.00" are damaged or unrelated numbers, not amounts,
        # and must never be reported as a price.
        if not _MONEY_FORM_RE.fullmatch(token):
            return ValueToken(token, "number", "unknown", digit_run, ("no currency context",))

    if resolved == "date":
        match = re.search(r"(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})", token)
        if not match:
            for pattern, fields in (
                (
                    r"(\d{1,2})\s+(" + "|".join(_MONTH_NAMES) + r")\s+(\d{4})",
                    ("day", "month", "year"),
                ),
                (
                    r"(" + "|".join(_MONTH_NAMES) + r")\s+(\d{1,2})\s*,?\s*(\d{4})",
                    ("month", "day", "year"),
                ),
            ):
                spelled = re.search(pattern, token, re.IGNORECASE)
                if not spelled:
                    continue
                parts = dict(zip(fields, spelled.groups()))
                day = int(parts["day"])
                month = _MONTH_NAMES.index(parts["month"].lower()) + 1
                year = int(parts["year"])
                if 1 <= day <= 31 and 1 <= month <= 12 and 1500 <= year <= 2900:
                    return ValueToken(
                        token, "date", "verified", f"{day:02d}/{month:02d}/{year}", ()
                    )
                return ValueToken(
                    token, "date", "corrupted", None, ("component out of range",)
                )
            return ValueToken(token, "date", "unknown", None, ())
        parts = [int(value) for value in match.groups()]
        day, middle, year = parts[0], parts[1], parts[2] if parts[2] >= 100 else parts[0]
        artifacts = tuple(sorted({ch for ch in token if not ch.isdigit() and ch not in "/.- "}))
        if not (1 <= middle <= 12) or not (1500 <= year <= 2900) or day > 31:
            return ValueToken(
                token, "date", "corrupted", None, artifacts or ("component out of range",)
            )
        return ValueToken(token, "date", "verified", token, ())

    if resolved == "identifier":
        clean = re.sub(r"\s+", "", token)
        if re.fullmatch(r"[A-Za-z0-9#/\-_.]+", clean):
            return ValueToken(token, "identifier", "verified", clean, ())
        return ValueToken(
            token, "identifier", "corrupted", clean,
            tuple(ch for ch in token if not re.match(r"[A-Za-z0-9#/_.\-]", ch)),
        )

    if not latin_letters and not other_letters and symbol_count <= 1:
        if _digit_only(token):
            if mixed_scripts:
                # "12१" is one damaged reading, not a clean number: a recognizer
                # that flipped between digit scripts cannot be trusted to have got
                # the glyphs right.
                return ValueToken(
                    token, resolved, "corrupted", digit_run, ("mixed digit scripts",)
                )
            return ValueToken(token, resolved, "verified", digit_run, ())
        return ValueToken(token, resolved, "corrupted", digit_run, ("decimal separator unreadable",))

    if other_letters and not latin_letters:
        # A non-Latin letter welded onto digits is a damaged currency glyph
        # ("इ७०"), so the digits may be reportable but the value is not verified.
        return ValueToken(token, resolved, "corrupted", digit_run, tuple(sorted(set(other_letters))))

    return ValueToken(token, resolved, "corrupted", digit_run, tuple(sorted(set(latin_letters))))


def extract_values(text: str) -> tuple[ValueToken, ...]:
    """Pull amount/date/identifier tokens out of a source window.

    Only tokens that live inside a single line are considered, so a value can
    never be invented across a line break. The one exception is a thousands group
    that the recogniser left dangling at the end of a line: that is one number
    split in two, and the two halves are reported as the whole reading.
    """
    found: list[ValueToken] = []
    seen: set[str] = set()

    lines = normalize_text(text).split("\n")

    fragments: set[str] = set()
    joined_amounts: list[str] = []
    for index in range(len(lines) - 1):
        head = _DANGLING_THOUSANDS_RE.search(lines[index])
        tail = _AMOUNT_HEAD_RE.match(lines[index + 1])
        if head and tail:
            joined_amounts.append(f"{head.group(1)},{tail.group(1)}")
            fragments.add(f"{head.group(1)},")
            fragments.add(tail.group(1))

    def add(value: ValueToken | None) -> None:
        if value is None or value.raw in seen or "\n" in value.raw:
            return
        if value.raw in fragments:
            return
        seen.add(value.raw)
        found.append(value)

    for raw in joined_amounts:
        add(classify_value(raw, "amount"))

    for line in lines:
        for match in _DATE_RE.finditer(line):
            add(classify_value(match.group(0), "date"))
        for match in _AMOUNT_RE.finditer(line):
            add(classify_value(match.group(0), "amount"))

        # Devanagari amounts often lose their currency glyph entirely ("इ७०").
        # A clean Devanagari digit run with no currency context is not promoted to
        # an amount: on these pages it is usually a line or serial number.
        line_amount_hint = bool(_AMOUNT_HINT_RE.search(line))
        for match in re.finditer(r"[^\s]*[०-९][^\s]*", line):
            value = classify_value(match.group(0))
            if value is None:
                continue
            if value.kind == "number":
                welded = any(
                    character.isalpha() and not ("०" <= character <= "९")
                    for character in value.raw
                )
                if welded or line_amount_hint:
                    state = "corrupted" if welded or value.state != "verified" else value.state
                    value = ValueToken(
                        value.raw,
                        "amount",
                        state,
                        value.normalized,
                        value.artifacts or ("no currency glyph",),
                    )
            add(value)

        # A bare decimal inside a table row is still that row's amount - but only
        # when the cell holds one number, not two welded together by OCR.
        for cell in re.split(r"[|\s]+", line):
            cell = cell.strip()
            if "." in cell and _digit_only(cell) and len(_digits(cell)) >= 2:
                add(classify_value(cell, "amount"))

        for match in _IDENTIFIER_RE.finditer(line):
            add(classify_value(match.group(0), "identifier"))

    return tuple(found)


def _fuzzy_threshold(term_length: int, relaxed: bool = False) -> float:
    if relaxed:
        # Only used inside a document the question already named: truncated OCR
        # labels such as "TOTA" for TOTAL or "MORNINC" for MORNING are admissible
        # there, and the ranking penalty plus verbatim quoting keep them honest.
        if term_length <= 5:
            return 0.75
        if term_length <= 7:
            return 0.72
        return 0.70
    if term_length <= 5:
        return 0.90
    if term_length <= 7:
        return 0.86
    return 0.82


def fuzzy_ratio(left: str, right: str) -> float:
    try:
        from rapidfuzz.fuzz import ratio

        return ratio(left, right) / 100.0
    except ImportError:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, left, right).ratio()


def _identifiers_compatible(left: str, right: str) -> bool:
    """Allow letter-level OCR damage inside an identifier, never digit damage.

    Two reference numbers that differ by one digit are different references, so a
    digit mismatch is an automatic rejection.
    """
    left_digits = re.findall(r"\d", left)
    right_digits = re.findall(r"\d", right)
    if not left_digits or not right_digits:
        return True
    if len(left_digits) != len(right_digits):
        return False
    return all(a == b for a, b in zip(left_digits, right_digits))


def _match_candidates(unit_tokens: Sequence[str]) -> list[str]:
    """Token parts a term can match, including hyphen and slash sub-words.

    ``NON-REFONDABLE`` is one ``\\w+`` token, so a question about a "non
    refundable" policy has to be able to reach inside it.
    """
    parts: list[str] = []
    for token in unit_tokens:
        parts.append(token)
        if any(separator in token for separator in "-_./"):
            parts.extend(
                piece for piece in re.split(r"[-_./]+", token) if len(piece) >= 2
            )
    return [piece for piece in parts if piece]


def match_term(
    term: str,
    unit_tokens: Sequence[str],
    unit_compact: str,
    relaxed: bool = False,
) -> tuple[str, float] | None:
    """Return the best OCR token in the window for a query term, or None.

    Tokens of three characters or fewer must match exactly; length-aware
    thresholds plus the identifier digit rule keep unrelated words and reference
    numbers apart. Exact matches always win over fuzzy ones. ``relaxed`` widens
    the ratio floor for abbreviation-style damage and is only ever applied inside
    a document the question already named.
    """
    if not term:
        return None

    best: tuple[str, float] | None = None
    for candidate in _match_candidates(unit_tokens):
        if not candidate:
            continue
        if candidate == term:
            return (candidate, 1.0)
        if (len(term) <= 3 or len(candidate) <= 3) and not (
            relaxed and min(len(term), len(candidate)) >= 3
        ):
            continue
        length_gap_ok = abs(len(term) - len(candidate)) <= max(1, len(term) // 4)
        containment = candidate in term or term in candidate
        if not length_gap_ok and not containment:
            continue
        if not _identifiers_compatible(term, candidate):
            continue
        score = fuzzy_ratio(term, candidate)
        floor = _fuzzy_threshold(len(term), relaxed)
        if containment and score < floor:
            # Missing-OCR-word-boundary cases such as CAR/CARE stay admissible
            # only when nearly the whole longer word is present.
            ratio_ok = 0.55 if relaxed else 0.72
            if min(len(term), len(candidate)) / max(len(term), len(candidate)) < ratio_ok:
                continue
            floor = min(floor, 0.86 if not relaxed else 0.72)
        if score >= floor:
            if best is None or score > best[1]:
                best = (candidate, score)

    if best is None and len(term) >= 5:
        # Word-boundary loss: "BATH TIME" -> "BATHTIM", "SELF CARE" -> "SELFCAR".
        compact_term = term.replace(" ", "")
        if compact_term in unit_compact:
            return (compact_term, 0.88)

    return best


def _hint_hit(hint: str, lowered: str, compact: str, tokens: set[str]) -> bool:
    """Whether an intent hint is really present in the question.

    Substring matching alone makes "non-refundable" look like a "ref" number
    question, so Latin hints must stand as their own word; Devanagari hints keep
    substring matching because spacing there is unreliable in OCR output.
    """
    normalized = normalize_token(hint)
    if not normalized:
        return False
    if " " in normalized:
        return normalized in lowered or normalized.replace(" ", "") in compact
    if normalized in tokens:
        return True
    if not normalized.isascii():
        return normalized in compact
    return False


def build_query_plan(question: str) -> QueryPlan:
    """Interpret a question without any document- or fixture-specific rules."""
    normalized = normalize_text(question or "")
    lowered = normalized.lower()
    all_tokens = tokenize(normalized)
    token_set = set(all_tokens)
    compact = "".join(all_tokens)

    weighted: list[tuple[str, float]] = []
    for token in dict.fromkeys(all_tokens):
        if len(token) < 3 or token in QUERY_STOPWORDS:
            continue
        if token.isdigit():
            continue
        weight = 1.0 if len(token) >= 6 else (0.8 if len(token) >= 4 else 0.5)
        if token in STRUCTURAL_WORDS:
            weight *= 0.45
        weighted.append((token, round(weight, 3)))

    # A reference number in the question is one term, not its punctuation pieces:
    # "inv-3337" only compares against "INV-3331" as a whole.
    for token in glued_tokens(normalized):
        if token in token_set or token in QUERY_STOPWORDS:
            continue
        weighted.append((token, 1.0))

    phrases: list[str] = []
    for size in (4, 3, 2):
        for start in range(0, max(0, len(all_tokens) - size + 1)):
            window = all_tokens[start : start + size]
            if any(token in QUERY_STOPWORDS for token in window):
                continue
            if not any(len(token) >= 4 for token in window):
                continue
            phrases.append(" ".join(window))

    # Whichever intent hint is the most specific wins. A question can name both a
    # money word and a date word ("pay", "last date") while only asking for the
    # date; hint length stands in for specificity because every hint is a whole
    # word or phrase of the question.
    def _best_hint_length(hints: tuple[str, ...]) -> int:
        return max(
            (
                len(normalize_token(hint))
                for hint in hints
                if _hint_hit(hint, lowered, compact, token_set)
            ),
            default=0,
        )

    hint_lengths = {
        "amount": _best_hint_length(_MONEY_HINTS),
        "date": _best_hint_length(_DATE_HINTS),
        "identifier": _best_hint_length(_IDENTIFIER_HINTS),
    }
    # A time interrogative states what is being asked for, so it outranks a money
    # verb that only says what the document is about: "when must the invoice be
    # paid" wants a date even though "when" and "paid" are equally specific words.
    asks_when = any(
        _hint_hit(hint, lowered, compact, token_set) for hint in _WHEN_INTERROGATIVES
    )
    value_kind: str | None = None
    best_length = 0
    if asks_when and hint_lengths["date"]:
        value_kind, best_length = "date", hint_lengths["date"]
    for kind in ("amount", "date", "identifier"):
        length = hint_lengths[kind]
        if length > best_length:
            value_kind, best_length = kind, length

    # A plural noun is often the *kind of value* being asked for ("what are the
    # electricity charges") rather than a request for the whole set. Once the
    # question already names a value type, only an explicit collection cue makes
    # it an aggregation - otherwise every document that happens to hold a number
    # of that kind joins the evidence pool and pushes out the one that matches.
    collection_cue = any(
        _hint_hit(hint, lowered, compact, token_set) for hint in _AGGREGATION_HINTS
    )
    yes_no = lowered.strip().startswith(
        ("does", "do", "is", "are", "was", "were", "did", "have", "has")
    )
    numeric_literals = tuple(re.findall(r"\d[\d.,/]{2,}", normalized))
    # A yes/no question asks whether a stated property holds, not for the value
    # itself, so it must not demand that the window also carry a number - the line
    # that answers it usually carries none. A question that quotes the figure it
    # wants checked keeps its value type.
    if yes_no and not numeric_literals:
        value_kind = None
    return QueryPlan(
        question=normalized,
        terms=tuple(token for token, _ in weighted),
        weighted_terms=tuple(weighted),
        phrases=tuple(dict.fromkeys(phrases)),
        value_kind=value_kind,
        aggregate=collection_cue
        or (value_kind is None and any(token in _LIST_NOUNS for token in all_tokens)),
        yes_no=yes_no,
        table_intent=any(
            _hint_hit(hint, lowered, compact, token_set) for hint in _TABLE_HINTS
        ),
        numeric_literals=numeric_literals,
    )


def filename_scope_tokens(filename: str) -> list[str]:
    """Tokens that could identify a document by name, minus container filler."""
    stem = normalize_text(filename).rsplit(".", 1)[0].lower()
    return [
        token
        for token in dict.fromkeys(_TOKEN_RE.findall(stem))
        if len(token) >= 4 and token not in _GENERIC_FILE_TOKENS
    ]


def filename_scope_keys(filename: str) -> set[str]:
    """Every name form a question could use to point at this document.

    ``File_04_pages_016-020.pdf`` is one ``\\w+`` token, so a question that says
    "File_04" would never match a whole-stem comparison. Splitting on separators
    and keeping the joined pairs recognises it without letting a bare container
    word ("file") or a short number select a document on its own.
    """
    stem = normalize_text(filename).rsplit(".", 1)[0].lower().strip()
    if not stem:
        return set()
    keys: set[str] = {stem, stem.replace(" ", "")}
    pieces = [piece for piece in re.split(r"[_\-\s.]+", stem) if piece]
    for index, piece in enumerate(pieces):
        if len(piece) >= 4 and piece not in _GENERIC_FILE_TOKENS:
            keys.add(piece)
        following = pieces[index + 1] if index + 1 < len(pieces) else ""
        if following:
            keys.add(f"{piece}_{following}")
            keys.add(f"{piece}{following}")
            if index + 2 < len(pieces):
                third = pieces[index + 2]
                keys.add(f"{piece}_{following}_{third}")
                keys.add(f"{piece}{following}{third}")
    return {key for key in keys if len(key) >= 4 and key not in _GENERIC_FILE_TOKENS}


def anchors_scope(key: str) -> bool:
    """Whether a filename fragment may narrow retrieval on its own.

    A bare word taken from a filename ("walk", "settlement", "neha") is normally
    the subject of the question rather than a reference to the file that happens
    to contain it, so only fragments that carry a digit or join two filename
    parts are allowed to pick the scope. The whole filename still anchors.
    """
    return "_" in key or any(character.isdigit() for character in key)


def resolve_document_scope(
    db: Session,
    question: str,
    user: Any | None = None,
    max_ambiguity: int = 2,
) -> list[int]:
    """Documents the question names explicitly, by filename token or type.

    A name fragment only anchors a document when it points at few enough
    documents to stay unambiguous (``max_ambiguity``); a word that would select
    most of the library is treated as content, not as a scope reference.
    Container words such as "file", "pages" or "sample" never anchor, and
    neither does a plain filename word that is also ordinary content ("walk"),
    because a subject word must not silently restrict retrieval to one file.
    Nothing here is document- or fixture-specific.
    """
    from app.models.document import Document
    from app.services.embedding_service import is_document_viewable

    lowered = normalize_text(question).lower()
    tokens = set(tokenize(question))
    # "File 04", "file04" and "file_04" all have to reach the same document, so
    # both sides of every comparison below are also read with the separators
    # removed - a space is as much of a separator as an underscore is.
    joined_question = join_separators(lowered)
    lookup = set(tokens) | {join_separators(token) for token in tokens}

    documents = [
        document
        for document in db.query(Document).order_by(Document.id).all()
        if user is None or is_document_viewable(db, document, user)
    ]

    direct: list[int] = []
    candidates: dict[str, list[int]] = {}
    for document in documents:
        filename = normalize_text(document.original_filename or "").lower()
        if not filename:
            continue
        if filename in lowered:
            direct.append(document.id)
            continue
        stem = filename.rsplit(".", 1)[0]
        if len(stem) >= 5 and (stem in lowered or join_separators(stem) in joined_question):
            direct.append(document.id)
            continue
        for key in filename_scope_keys(filename):
            if not anchors_scope(key):
                continue
            joined_key = join_separators(key)
            if key in lookup or joined_key in lookup or (
                len(joined_key) >= 5 and joined_key in joined_question
            ):
                candidates.setdefault(key, []).append(document.id)

    anchored = list(dict.fromkeys(direct))
    for matched_ids in candidates.values():
        if len(matched_ids) <= max_ambiguity:
            anchored.extend(matched_ids)

    if not anchored:
        for document in documents:
            doc_type = normalize_text(document.document_type or "").lower()
            if len(doc_type) >= 4 and doc_type in lowered:
                anchored.append(document.id)

    return list(dict.fromkeys(anchored))


def document_frequency(
    units: Sequence[EvidenceUnit],
    term: str,
) -> int:
    """How many distinct documents in the pool contain the term."""
    documents: set[int] = set()
    for unit in units:
        haystack = " ".join(tokenize(unit.text))
        compact = haystack.replace(" ", "")
        if f" {term} " in f" {haystack} " or term in compact:
            documents.add(unit.document_id)
    return len(documents)


def specific_terms(
    plan: QueryPlan,
    units: Sequence[EvidenceUnit],
    document_count: int,
) -> set[str]:
    """Query terms rare enough inside this corpus to identify relevant evidence.

    Document-frequency based, so it adapts to any library instead of relying on a
    hand-maintained word list. A term is specific when at most ~30% of documents
    (capped at 3) contain it.
    """
    if not units:
        return set()
    ceiling = max(1, min(3, math.ceil(document_count * 0.3)))
    out: set[str] = set()
    cache: dict[str, int] = {}
    for term in plan.terms:
        if term in STRUCTURAL_WORDS:
            continue
        if term not in cache:
            cache[term] = document_frequency(units, term)
        if cache[term] <= ceiling:
            out.add(term)
    if not out:
        # Nothing is rare in this corpus. Fall back to the longest content words
        # only - a function word that happens to be rare must not be treated as
        # evidence of relevance.
        content = [
            term
            for term in plan.terms
            if term not in QUERY_STOPWORDS and term not in STRUCTURAL_WORDS
        ]
        out.update(sorted(content, key=len, reverse=True)[:2])
    return out


def score_unit(
    unit: EvidenceUnit,
    plan: QueryPlan,
    specific: set[str] | None = None,
    relaxed: bool = False,
) -> EvidenceUnit:
    """Score one window against the plan: exact first, fuzzy penalised."""
    if not plan.terms:
        return unit

    tokens = tokenize(unit.text)
    compact = "".join(tokens)
    matchable = list(dict.fromkeys(tokens + glued_tokens(unit.text)))
    matched: list[str] = []
    hit_specific: list[str] = []
    fuzzy: list[tuple[str, str, float]] = []
    earned = 0.0

    weights = dict(plan.weighted_terms)
    possible = sum(weights.values()) or 1.0

    for term in plan.terms:
        weight = weights.get(term, 0.5)
        found = match_term(term, tokens, compact, relaxed)
        if found is None:
            continue
        candidate, ratio = found
        matched.append(term if candidate == term else candidate)
        if candidate == term:
            earned += weight
            if specific and term in specific:
                hit_specific.append(term)
        else:
            fuzzy.append((term, candidate, round(ratio, 3)))
            earned += weight * (0.65 if not relaxed else 0.45)
            if specific and term in specific and ratio >= 0.9:
                hit_specific.append(term)

    phrase_bonus = 0.0
    for phrase in plan.phrases:
        if phrase in compact or phrase.replace(" ", "") in compact:
            phrase_bonus = 0.15
            break

    if unit.header:
        header_tokens = set(tokenize(" ".join(unit.header)))
        if header_tokens and any(term in header_tokens for term in plan.terms):
            phrase_bonus = max(phrase_bonus, 0.15)

    unit.coverage = round(min(1.0, earned / possible + phrase_bonus), 4)
    unit.strength = round(min(1.0, earned / 1.6), 4)
    unit.matched_terms = tuple(dict.fromkeys(matched))
    unit.specific_matches = tuple(dict.fromkeys(hit_specific))
    unit.fuzzy_terms = tuple(fuzzy)
    unit.relaxed = bool(relaxed and matched)
    return unit


def iter_line_windows(
    text: str,
    max_lines: int = 4,
    max_chars: int = 170,
    step: int = 1,
) -> list[tuple[int, tuple[str, ...]]]:
    """Sliding windows over consecutive OCR lines, plus every single line.

    Each entry carries the page line it starts at, so a citation and a
    label-to-value association can be traced back to the source order even
    though no box geometry is persisted.
    """
    lines = [line.strip() for line in normalize_text(text).split("\n") if line.strip()]
    if not lines:
        return []

    windows: list[tuple[int, tuple[str, ...]]] = []
    seen: set[tuple[str, ...]] = set()
    for start in range(0, len(lines), max(1, step)):
        current: list[str] = []
        length = 0
        for offset in range(max_lines):
            index = start + offset
            if index >= len(lines):
                break
            candidate = lines[index]
            if current and length + len(candidate) > max_chars:
                break
            current.append(candidate)
            length += len(candidate)
            key = tuple(current)
            if key not in seen:
                seen.add(key)
                windows.append((start, key))
    return windows


def build_page_units(
    document_id: int,
    document_name: str,
    page_number: int,
    text: str,
    chunk_index: int = 0,
    semantic_raw: float = 0.0,
    anchored: bool = False,
    max_units: int = 600,
) -> list[EvidenceUnit]:
    """Turn one page of OCR text into table rows plus reading-order windows.

    The page chunk itself is kept as a unit so paraphrase questions that no
    lexical term can reach still have their semantic evidence available.
    """
    source = normalize_text(text)
    if not source.strip():
        return []

    common = dict(
        document_id=document_id,
        document_name=document_name,
        page_number=page_number,
        chunk_index=chunk_index,
        semantic_raw=semantic_raw,
        anchored=anchored,
    )

    units: list[EvidenceUnit] = [
        EvidenceUnit(
            text=source.strip(),
            lines=(source.strip(),),
            source_kind="chunk",
            **common,
        )
    ]

    header: tuple[str, ...] = ()
    in_block = False
    lines = [line.strip() for line in source.split("\n") if line.strip()]
    for position, line in enumerate(lines):
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|")]
            filled = [cell for cell in cells if cell]
            if not in_block:
                if len(filled) < 2:
                    continue
                header = tuple(cells)
                in_block = True
                kind = "table_header"
            else:
                kind = "table_row"
            units.append(
                EvidenceUnit(
                    text=line,
                    lines=(line,),
                    header=header if kind == "table_row" else (),
                    is_table_row=True,
                    source_kind=kind,
                    line_index=position,
                    **common,
                )
            )
            continue

        in_block = False
        header = ()

    # Deterministic label-to-value entries: the lines immediately above a value
    # line, back to the previous value line. Sliding windows overlap, so a window
    # can put one row's amount under the next row's label; this segmentation
    # cannot, and it is the only association the source order actually supports.
    line_values = [
        () if "|" in line else extract_values(line) for line in lines
    ]
    value_flags = [bool(values) for values in line_values]

    for start, window in iter_line_windows(source)[:max_units]:
        if any("|" in line for line in window):
            continue
        # A window that carries a value line before its final line would attach
        # that value to whatever follows it. The entry units already own that
        # pairing, so the overlap is dropped instead of being ranked.
        if any(value_flags[start : start + len(window) - 1]):
            continue
        units.append(
            EvidenceUnit(
                text="\n".join(window),
                lines=window,
                source_kind="line_window",
                line_index=start,
                **common,
            )
        )

    run_start = 0
    for position, line in enumerate(lines):
        if "|" in line:
            run_start = position + 1
            continue
        amounts = [value for value in line_values[position] if value.kind == "amount"]
        if not line_values[position] or len(amounts) > 1:
            continue
        labels = lines[run_start:position][-3:]
        block = tuple(labels + [line])
        units.append(
            EvidenceUnit(
                text="\n".join(block),
                lines=block,
                source_kind="entry",
                line_index=position - len(labels),
                **common,
            )
        )
        run_start = position + 1

    for unit in units:
        unit.values = extract_values(unit.text)
    return units


def unit_rank_score(unit: EvidenceUnit, plan: QueryPlan) -> float:
    """Ranking-only score for a grounded unit.

    Rewards the tight, association-preserving windows and penalizes whole-page
    chunks: a chunk can pass the support floor on a single token and then hand
    the extractor a page of unrelated lines. This never gates answerability -
    ``EvidenceUnit.is_grounded`` does that with the raw signals only.
    """
    score = unit.coverage + unit.strength * 0.35
    if unit.matched_terms:
        # Contact with the question's own wording is what separates the labelled
        # value from every other figure on the same page, and a damaged label is
        # still contact. A window that matches nothing is admitted only by marginal
        # similarity, so it must not outscore one that does, even after the fuzzy
        # penalties below. Ranking only: is_grounded still decides answerability.
        score += 0.25
    if unit.specific_matches:
        score += 0.1
    if unit.anchored:
        # A small preference for the document the question (or the selector) named.
        # It has to stay below what a real term match is worth: a flat bonus large
        # enough to lift a window that matches nothing above one that carries the
        # question's own wording makes every value in the file tie, and the answer
        # abstains instead of reading the labelled figure.
        score += 0.1
    if unit.source_kind == "chunk":
        score -= 0.35
    if unit.source_kind == "entry":
        # The segmentation that keeps a value under its own label.
        score += 0.25
    if unit.is_table_row:
        score += 0.15
        if unit.header:
            score += 0.15
    if plan.value_kind:
        verified = unit.requested_verified(plan)
        candidates = unit.requested_values(plan)
        if len(verified) == 1:
            score += 0.2
        elif len(verified) > 1:
            score -= 0.15
        elif len(candidates) == 1:
            # Only a damaged reading exists: usable, but it must not outrank a
            # window that carries a clean number.
            score += 0.05
    if plan.table_intent and (unit.is_table_row or unit.header):
        score += 0.3
    if plan.table_intent and unit.source_kind == "table_header":
        score += 0.5
    if plan.aggregate and unit.looks_like_list_item():
        score += 0.2
    if unit.relaxed:
        score -= 0.15
    if unit.fuzzy_terms and not unit.specific_matches:
        score -= 0.1
    return score


def rank_units(
    units: Iterable[EvidenceUnit],
    plan: QueryPlan,
    specific: set[str] | None = None,
    limit: int = 24,
) -> list[EvidenceUnit]:
    """Rank grounded evidence: association quality first, similarity as tie-break.

    Only grounded units are returned, so a marginally similar unrelated document
    cannot occupy a slot that the extractor would then answer from.
    """
    scored = []
    for unit in units:
        score_unit(unit, plan, specific)
        if unit.anchored and unit.coverage < LEXICAL_RANK_FLOOR:
            # Inside a document the question named, abbreviation-style OCR damage
            # is admissible; everywhere else it is not.
            score_unit(unit, plan, specific, relaxed=True)
        scored.append(unit)
    grounded = [unit for unit in scored if unit.is_grounded(plan)]
    grounded.sort(
        key=lambda unit: (
            unit_rank_score(unit, plan),
            -len(unit.text),
            unit.semantic_raw,
            unit.document_id,
            unit.page_number,
        ),
        reverse=True,
    )
    if plan.aggregate:
        return _aggregate_units(grounded, plan, limit)
    return dedupe_units(grounded)[:limit]


def _support_tier(unit: EvidenceUnit, plan: QueryPlan) -> int:
    """0 = this window answers the question's own wording, 1 = it only holds a value.

    A collection question must reach every contributing document, but a document
    that qualifies only because it carries a number or a row of the requested
    kind must not spend the evidence budget of one the question actually names.
    """
    if unit.anchored:
        return 0
    if unit.lexically_supported(plan) or unit.semantic_supported():
        return 0
    return 1


def _aggregate_units(
    units: Sequence[EvidenceUnit],
    plan: QueryPlan,
    limit: int,
) -> list[EvidenceUnit]:
    """Choose aggregate evidence by strength, then present it in reading order.

    ``units`` arrives already ranked. Slots go to documents the question matches
    first and only then to the ones that merely contribute an entry; sorting by
    document id before this choice is what let an unrelated file occupy the last
    slots and drop the matching document out of the pool. Within a document the
    windows keep page and line order, so a collection still reads down the page as
    the source does, and documents lead with the one holding the strongest answer.
    """
    primary = [unit for unit in units if _support_tier(unit, plan) == 0]
    secondary = [unit for unit in units if _support_tier(unit, plan) == 1]
    chosen = dedupe_units(
        _spread_across_documents(primary, per_document=8)
        + _spread_across_documents(secondary, per_document=8)
    )[:limit]
    best_by_document: dict[int, float] = {}
    for unit in chosen:
        score = unit_rank_score(unit, plan)
        if score > best_by_document.get(unit.document_id, float("-inf")):
            best_by_document[unit.document_id] = score
    chosen.sort(
        key=lambda unit: (
            -best_by_document[unit.document_id],
            unit.document_id,
            unit.page_number,
            unit.line_index,
        )
    )
    return chosen


def _spread_across_documents(
    units: Sequence[EvidenceUnit],
    per_document: int = 6,
) -> list[EvidenceUnit]:
    """Cap one document's share so a cross-document answer sees every document.

    "Every date across my documents" is only answered correctly if each document
    contributes; otherwise the longest table floods the pool.
    """
    picked: list[EvidenceUnit] = []
    counts: dict[int, int] = {}
    for unit in units:
        if counts.get(unit.document_id, 0) >= per_document:
            continue
        counts[unit.document_id] = counts.get(unit.document_id, 0) + 1
        picked.append(unit)
    return picked


def dedupe_units(units: Sequence[EvidenceUnit]) -> list[EvidenceUnit]:
    """Keep the tightest window per distinct token sequence."""
    kept: list[EvidenceUnit] = []
    seen: set[str] = set()
    for unit in units:
        if unit.source_kind == "chunk" and any(
            other.document_id == unit.document_id
            and other.page_number == unit.page_number
            and other.source_kind != "chunk"
            for other in kept
        ):
            # Keep the page chunk only when no finer window survived for it.
            continue
        key = " ".join(tokenize(unit.text))
        if not key or key in seen:
            continue
        tokens = tokenize(unit.text)
        subsumed = any(
            kept_unit.document_id == unit.document_id
            and kept_unit.page_number == unit.page_number
            and kept_unit.matched_terms == unit.matched_terms
            and kept_unit.value_tokens == unit.value_tokens
            and (
                _is_contiguous(tokenize(kept_unit.text), tokens)
                or _is_contiguous(tokens, tokenize(kept_unit.text))
            )
            for kept_unit in kept
        )
        if subsumed:
            # One window inside another, matching the same terms and carrying the
            # same values, is the same evidence twice.
            continue
        seen.add(key)
        kept.append(unit)
    return kept


def _is_contiguous(needles: Sequence[str], haystack: Sequence[str]) -> bool:
    """True when ``needles`` appears as a run inside ``haystack`` (not equal)."""
    length = len(needles)
    if not length or length >= len(haystack):
        return False
    return any(
        list(haystack[start : start + length]) == list(needles)
        for start in range(len(haystack) - length + 1)
    )


def scope_document_ids(
    db: Session,
    user: Any | None,
    document_ids: Sequence[int] | None,
) -> tuple[list[int], list[int]]:
    """Resolve an explicit scope to viewable document ids.

    Returns (allowed_ids, rejected_ids). Rejected ids contribute no content, so
    an unauthorized id cannot be probed through the chatbot.
    """
    from app.models.document import Document
    from app.services.embedding_service import is_document_viewable

    if not document_ids:
        return [], []

    requested = list(dict.fromkeys(int(value) for value in document_ids))
    allowed: list[int] = []
    rejected: list[int] = []
    for document_id in requested:
        document = db.query(Document).filter(Document.id == document_id).first()
        if document is None or (user is not None and not is_document_viewable(db, document, user)):
            rejected.append(document_id)
            continue
        if (document.status or "").lower() in ("failed", "processing"):
            rejected.append(document_id)
            continue
        allowed.append(document_id)
    return allowed, rejected


def collect_evidence(
    db: Session,
    plan: QueryPlan,
    user: Any | None = None,
    document_ids: Sequence[int] | None = None,
    anchored_ids: Sequence[int] | None = None,
    max_units: int = 24,
) -> tuple[list[EvidenceUnit], dict[str, Any]]:
    """Gather ranked evidence units from authorized OCR text.

    Semantic retrieval supplies raw similarity per page (and recall for paraphrase
    questions); the lexical pass over persisted OCR text supplies the exact
    evidence that semantic similarity alone misses on damaged text. Retrieval
    stays at a wider recall floor than the answer gate, which is applied per unit.
    """
    from app.models.document import Document, DocumentPage, OCRResult
    from app.services.embedding_service import is_document_viewable, semantic_search

    query = plan.question
    diagnostics: dict[str, Any] = {
        "semantic_pages": 0,
        "pages_scanned": 0,
        "documents_in_scope": 0,
        "anchored_ids": list(anchored_ids or []),
        "explicit_scope": list(document_ids or []),
        "retrieval_empty": False,
    }
    if not query.strip():
        diagnostics["retrieval_empty"] = True
        return [], diagnostics

    anchored_set = {int(value) for value in (anchored_ids or [])}
    scope_ids = [int(value) for value in (document_ids or [])]
    if not scope_ids and 0 < len(anchored_set) <= _MAX_ANCHORED_SCOPE:
        # A question that names its document has already chosen the scope.
        # Retrieving the rest of the library alongside it only gives an unrelated
        # document the chance to win on a marginal similarity.
        scope_ids = sorted(anchored_set)
    diagnostics["effective_scope"] = scope_ids

    semantic_by_page: dict[tuple[int, int], float] = {}
    try:
        search = semantic_search(
            db,
            query,
            top_k=12,
            min_score=0.15,
            user=user,
            aggregate_by_document=False,
            document_ids=scope_ids or None,
        )
        for hit in search.items:
            key = (hit.document_id, int(hit.page_number or 1))
            semantic_by_page[key] = max(
                semantic_by_page.get(key, 0.0), float(hit.similarity or 0.0)
            )
    except Exception:
        logger.exception("Semantic evidence pass failed; continuing with lexical evidence")
    diagnostics["semantic_pages"] = len(semantic_by_page)

    document_query = db.query(Document)
    if scope_ids:
        document_query = document_query.filter(Document.id.in_(scope_ids))
    documents = {document.id: document for document in document_query.all()}
    diagnostics["documents_in_scope"] = len(documents)

    rows = db.query(OCRResult, DocumentPage).outerjoin(
        DocumentPage, DocumentPage.id == OCRResult.page_id
    )
    if documents:
        rows = rows.filter(OCRResult.document_id.in_(list(documents)))
    else:
        rows = rows.filter(OCRResult.document_id.in_([-1]))

    units: list[EvidenceUnit] = []
    for ocr, page in rows.all():
        document = documents.get(ocr.document_id)
        if document is None:
            continue
        if user is not None and not is_document_viewable(db, document, user):
            continue
        text = ocr.extracted_text or ""
        if not text.strip():
            continue
        page_number = int(page.page_number) if page is not None else 1
        units.extend(
            build_page_units(
                document_id=document.id,
                document_name=document.original_filename,
                page_number=page_number,
                text=text,
                semantic_raw=semantic_by_page.get((document.id, page_number), 0.0),
                anchored=document.id in anchored_set or bool(scope_ids),
            )
        )
        diagnostics["pages_scanned"] += 1

    if not units:
        diagnostics["retrieval_empty"] = True
        return [], diagnostics

    specific = specific_terms(plan, units, diagnostics["documents_in_scope"] or 1)
    ranked = rank_units(units, plan, specific=specific, limit=max_units)
    diagnostics["specific_terms"] = sorted(specific)
    if not ranked:
        diagnostics["retrieval_empty"] = False
    return ranked, diagnostics


def front_matter_units(
    db: Session,
    document_ids: Sequence[int],
    user: Any | None = None,
    max_lines: int = 14,
    max_documents: int = 4,
) -> list[EvidenceUnit]:
    """The opening lines of the documents the caller already selected.

    A question about what a document *is* about quotes no wording found inside it,
    so the evidence gate has nothing to match and the normal passes return nothing.
    The front of the first page is where a title, an addressee or a subject is
    printed, so it is read straight from the persisted OCR text of exactly these
    document ids. No similarity is claimed for it and no threshold is lowered: the
    unit says in words that it is front matter, and a document the caller did not
    select cannot appear here.
    """
    from app.models.document import Document, DocumentPage, OCRResult
    from app.services.embedding_service import is_document_viewable

    units: list[EvidenceUnit] = []
    for document_id in list(dict.fromkeys(int(value) for value in document_ids))[
        :max_documents
    ]:
        document = db.query(Document).filter(Document.id == document_id).first()
        if document is None:
            continue
        if user is not None and not is_document_viewable(db, document, user):
            continue
        rows = (
            db.query(OCRResult, DocumentPage)
            .outerjoin(DocumentPage, DocumentPage.id == OCRResult.page_id)
            .filter(OCRResult.document_id == document_id)
            .all()
        )
        if not rows:
            continue
        ocr, page = sorted(
            rows,
            key=lambda row: (
                int(row[1].page_number) if row[1] is not None else 0,
                row[0].id,
            ),
        )[0]
        lines = [
            line.strip()
            for line in normalize_text(ocr.extracted_text or "").split("\n")
            if line.strip()
        ][:max_lines]
        if not lines:
            continue
        units.append(
            EvidenceUnit(
                document_id=document.id,
                document_name=document.original_filename,
                page_number=int(page.page_number) if page is not None else 1,
                chunk_index=0,
                text="\n".join(lines),
                lines=tuple(lines),
                anchored=True,
                source_kind="front_matter",
            )
        )
    return units


def aggregate_values(
    units: Sequence[EvidenceUnit],
    kind: str,
    per_document_limit: int = 15,
) -> list[tuple[ValueToken, EvidenceUnit]]:
    """Collect distinct values of one kind across the whole evidence pool."""
    results: list[tuple[ValueToken, EvidenceUnit]] = []
    seen: set[tuple[int, str]] = set()
    per_document: dict[int, int] = {}

    for unit in units:
        if unit.source_kind == "chunk":
            continue
        for value in unit.values:
            if value.kind != kind or value.state == "unknown":
                continue
            key = (unit.document_id, value.raw)
            if key in seen:
                continue
            if per_document.get(unit.document_id, 0) >= per_document_limit:
                break
            seen.add(key)
            per_document[unit.document_id] = per_document.get(unit.document_id, 0) + 1
            results.append((value, unit))

    results.sort(key=lambda pair: (pair[1].document_id, pair[1].page_number, pair[0].raw))
    return results


def table_header_units(units: Sequence[EvidenceUnit]) -> list[EvidenceUnit]:
    """Detected table header rows, in document and page order."""
    headers = [unit for unit in units if unit.source_kind == "table_header"]
    headers.sort(key=lambda unit: (unit.document_id, unit.page_number))
    return headers


def list_item_units(units: Sequence[EvidenceUnit], limit: int = 12) -> list[EvidenceUnit]:
    """Windows that read like one list entry: a label plus at most one value."""
    picked: list[EvidenceUnit] = []
    seen: set[str] = set()
    for unit in units:
        if not unit.looks_like_list_item():
            continue
        key = " ".join(tokenize(unit.text))
        if key in seen:
            continue
        seen.add(key)
        picked.append(unit)
        if len(picked) >= limit:
            break
    return picked

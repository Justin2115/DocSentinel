import io
import html as html_module
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Disable unstable oneDNN primitive execution on CPU
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_use_onednn"] = "0"

import numpy as np
from PIL import Image, ImageFilter

from app.services.pipeline_metrics import stage
from app.services.table_structure import (
    NUMERIC_CELL_RE,
    TableStructure,
    from_paddle_html_rows,
    from_pymupdf_table,
    from_ruled_grid,
)
from app.services.table_quality import screen as screen_structures

logger = logging.getLogger(__name__)


# Supported language codes
SUPPORTED_LANGUAGES = {"auto", "en", "hi", "mr", "en+hi", "en+mr"}

# Distinctive characters specific to Marathi orthography in Devanagari
MARATHI_DISTINCTIVE_CHARS = set("\u0933\u0931\u0972")  # ळ, ऱ, ॲ

# High-frequency functional Marathi words/particles
MARATHI_WORDS = {
    "आहे", "आहेत", "नाही", "नाहीत", "होता", "होती", "होते", "होत्या",
    "झाला", "झाली", "झाले", "झालेल्या", "केला", "केली", "केले",
    "हा", "ही", "हे", "या", "तो", "ती", "ते", "त्या",
    "माझा", "माझी", "माझे", "माझ्या", "त्याचा", "त्याची", "त्याचे", "त्याच्या", "त्यांची",
    "आमचा", "आमची", "आमचे", "आमच्या", "तुमचा", "तुमची", "तुमचे", "तुमच्या",
    "आम्ही", "तुम्ही", "आपण", "स्वतः",
    "काय", "कोण", "कसा", "कशी", "कसे", "कुठे", "कधी", "कशाला", "कशास", "कुणास", "कोणाला",
    "मध्ये", "वर", "खाली", "चा", "ची", "चे", "च्या", "स", "ला", "ना", "कडून", "मुळे", "पर्यंत",
    "पेक्षा", "साठी", "बद्दल", "समोर", "आणि", "व", "पण", "परंतु", "किंवा", "म्हणून", "तरी",
    "जर", "तर", "कारण", "उगाच", "किती", "इतकी", "इतका", "इतके", "आधी", "नंतर", "फक्त",
    "दोन", "तीन", "चार", "पाच", "ओळी", "ओळ", "खरा", "खरी", "खरे", "खोटा", "खोटी", "खोटे",
    "सुरी", "घाई", "शाई", "बरा", "ठाऊक", "सुकते", "येतात", "गेली", "गेला", "गेले",
    "दस्तऐवज", "चाचणी", "पत्ता", "नाव", "दिनांक", "पत्ता:", "नाव:", "दिनांक:", "स्वाक्षरी",
    "महाराष्ट्र", "मुंबई", "पुणे", "जिल्हा", "तालुका", "गावाचे"
}

# High-frequency functional Hindi words/particles
HINDI_WORDS = {
    "है", "हैं", "नहीं", "था", "थी", "थे", "थीं",
    "हुआ", "हुई", "हुए", "होना", "होने", "किया", "की", "किए", "करने", "करते",
    "यह", "वह", "ये", "वे", "इस", "उस", "इन", "उन",
    "मैं", "हम", "तुम", "आप", "मेरा", "मेरी", "मेरे",
    "उसका", "उसकी", "उसके", "इसका", "इसकी", "इसके", "उनका", "उनकी", "उनके",
    "हमारा", "हमारी", "हमारे", "आपका", "आपकी", "आपके",
    "क्या", "कौन", "कैसा", "कैसी", "कैसे", "कहाँ", "कब", "क्यों", "किसी", "किसको", "किसे",
    "में", "पर", "का", "के", "को", "से", "द्वारा", "लिए", "तक", "अपेक्षा", "बारे",
    "और", "तथा", "एवं", "लेकिन", "परंतु", "किंतु", "या", "अथवा", "इसलिए", "फिर", "यदि", "तो", "क्योंकि",
    "कितना", "कितनी", "कितने", "इतना", "इतनी", "इतने", "पहले", "बाद", "केवल", "सिर्फ",
    "दस्तावेज़", "दस्तावेज", "परीक्षण", "सत्यापित", "रहे", "रहा", "रही", "पता", "नाम", "दिनांक",
    "पता:", "नाम:", "दिनांक:", "हस्ताक्षर", "भारत", "दिल्ली", "उत्तर", "प्रदेश", "सड़क"
}

# Initialize OCR engines lazily
_rapid_ocr_engine = None
_paddle_devanagari_engine = None
_paddle_english_engine = None


def get_rapid_ocr_engine():
    """Lazy loader for RapidOCR (English baseline)."""
    global _rapid_ocr_engine
    if _rapid_ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _rapid_ocr_engine = RapidOCR()
        except Exception as e:
            logger.warning(f"Could not initialize RapidOCR engine: {e}")
            _rapid_ocr_engine = False
    return _rapid_ocr_engine if _rapid_ocr_engine is not False else None


def _ensure_paddle_cpu_stability():
    """Ensure PaddlePaddle CPU inference disables oneDNN IR passes that cause primitive errors on Windows CPU."""
    # torch must be in memory before Paddle loads, or sentence-transformers
    # becomes unimportable for the rest of the process (see ml_runtime).
    from app.services.ml_runtime import ensure_torch_loaded

    ensure_torch_loaded()
    try:
        import paddle.inference as paddle_infer

        if not getattr(paddle_infer.Config, "_docsentinel_patched", False):
            _orig_switch_ir_optim = paddle_infer.Config.switch_ir_optim

            def _safe_switch_ir_optim(self, flag):
                return _orig_switch_ir_optim(self, False)

            paddle_infer.Config.switch_ir_optim = _safe_switch_ir_optim
            paddle_infer.Config._docsentinel_patched = True
    except Exception:
        pass


def get_paddle_devanagari_engine():
    """Lazy loader for PaddleOCR Multilingual Devanagari engine."""
    global _paddle_devanagari_engine
    if _paddle_devanagari_engine is None:
        try:
            _ensure_paddle_cpu_stability()
            from paddleocr import PaddleOCR

            # Initialize PaddleOCR with Devanagari recognition model, CPU execution, stable CPU inference
            with stage("engine.paddle_init", engine="devanagari"):
                _paddle_devanagari_engine = PaddleOCR(
                    use_angle_cls=False,
                    lang="devanagari",
                    show_log=False,
                    enable_mkldnn=False,
                    use_gpu=False,
                )
        except Exception as e:
            logger.warning(f"Could not initialize PaddleOCR Devanagari engine: {e}")
            _paddle_devanagari_engine = False
    return _paddle_devanagari_engine if _paddle_devanagari_engine is not False else None


def get_paddle_english_engine():
    """Lazy loader for PaddleOCR English engine."""
    global _paddle_english_engine
    if _paddle_english_engine is None:
        try:
            _ensure_paddle_cpu_stability()
            from paddleocr import PaddleOCR

            with stage("engine.paddle_init", engine="english"):
                _paddle_english_engine = PaddleOCR(
                    use_angle_cls=False,
                    lang="en",
                    show_log=False,
                    enable_mkldnn=False,
                    use_gpu=False,
                )
        except Exception as e:
            logger.warning(f"Could not initialize PaddleOCR English engine: {e}")
            _paddle_english_engine = False
    return _paddle_english_engine if _paddle_english_engine is not False else None


_paddle_table_engine: Any = None


def get_paddle_table_engine():
    """Lazy loader for the PP-Structure table-recognition pipeline.

    Structure + numeric cells come from the English table model; Devanagari
    text cells are re-read from cell crops with the Devanagari engine because
    PP-Structure only supports en/ch built-in recognition.
    """
    global _paddle_table_engine
    if _paddle_table_engine is None:
        try:
            _ensure_paddle_cpu_stability()
            from paddleocr import PPStructure

            with stage("engine.paddle_init", engine="pp_structure_table"):
                _paddle_table_engine = PPStructure(
                    table=True,
                    ocr=False,
                    layout=False,
                    show_log=False,
                    lang="en",
                )
        except Exception as e:
            logger.warning(f"Could not initialize PP-Structure table engine: {e}")
            _paddle_table_engine = False
    return _paddle_table_engine if _paddle_table_engine is not False else None





@dataclass
class ExtractedPage:
    page_number: int
    text: str
    # None means the engine produced no measurable word confidence (digital
    # text layer, blank page); it is not a score of 0 and not a hidden 98.
    confidence: float | None
    detected_language: str = "en"
    width: int | None = None
    height: int | None = None
    page_path: str | None = None
    status_stamps: list[str] = field(default_factory=list)
    is_duplicate: bool = False
    duplicate_of_page: int | None = None
    raw_text: str = ""
    page_type: str = "digital"  # digital | scanned | blank
    ocr_retried: bool = False
    ocr_failed: bool = False
    table_count: int = 0
    engine: str = ""
    # TableStructure objects for this page (see app.services.table_structure).
    tables: list[Any] = field(default_factory=list)
    line_boxes: list[Any] = field(default_factory=list)


@dataclass
class DocumentExtractionResult:
    pages: list[ExtractedPage] = field(default_factory=list)
    combined_text: str = ""
    overall_ocr_confidence: float = 0.0
    ocr_engine: str = "direct_extraction"
    detected_language: str = "en"
    is_ocr_applied: bool = False
    ocr_failed_pages: int = 0


def _is_detached_devanagari(token: str) -> bool:
    """True for a single Devanagari character printed on its own."""
    return len(token) == 1 and "\u0900" <= token <= "\u097F"


def _recombine_spaced_devanagari(chunk: str) -> str:
    """Glue detached Devanagari glyphs without welding real words together.

    A broken line reads "क ा म" and needs no spaces; a Marathi phrase reads
    "काम वर्णन" and its space is part of the text. So a space is removed only
    between two Devanagari characters when one of them stands alone - the shape a
    dropped matra, virama or nukta leaves behind.
    """
    tokens = [token for token in chunk.split(" ") if token]
    if len(tokens) < 2:
        return chunk
    joined = tokens[0]
    for index in range(1, len(tokens)):
        previous, token = tokens[index - 1], tokens[index]
        between_glyphs = ("\u0900" <= previous[-1] <= "\u097F") and (
            "\u0900" <= token[0] <= "\u097F"
        )
        detached = _is_detached_devanagari(previous) or _is_detached_devanagari(token)
        joined += token if between_glyphs and detached else " " + token
    return joined


def normalize_devanagari_text(text: str) -> str:
    """Recombine dropped or detached Devanagari vowel signs (matras), virama, anusvara, and Marathi characters."""
    if not text:
        return ""
    import unicodedata

    # Rejoin words and identifiers split by hyphen across lines
    text = re.sub(r"([A-Za-z0-9\u0900-\u097F])-\s*\n\s*([A-Za-z0-9\u0900-\u097F])", r"\1\2", text)
    if any("\u0900" <= c <= "\u097F" for c in text):
        # 1. Recombine character-spaced Devanagari words
        if "  " in text:
            lines = []
            for line in text.split("\n"):
                words = re.split(r"[ \t]{2,}", line)
                fixed = [_recombine_spaced_devanagari(w) for w in words]
                lines.append("   ".join(fixed))
            text = "\n".join(lines)
        else:
            raw_tokens = text.strip().split()
            if len(raw_tokens) > 2 and all(len(tok) == 1 for tok in raw_tokens):
                text = re.sub(r"(?<=[\u0900-\u097F])\s+(?=[\u0900-\u097F])", "", text)

        # 2. Spurious space before Devanagari vowel signs (matras), nukta, anusvara, visarga
        # Combining marks: \u0901-\u0903, \u093c, \u093e-\u094d, \u0962-\u0963
        text = re.sub(
            r"([\u0900-\u0939\u0958-\u0961])\s+([\u0901-\u0903\u093c\u093e-\u094d\u0962\u0963])",
            r"\g<1>\g<2>",
            text,
        )
        # 3. Virama (halant) split across consonants: e.g. "क ् ष" -> "क्ष"
        text = re.sub(
            r"([\u0900-\u0939\u0958-\u0961])\s*\u094d\s*([\u0900-\u0939\u0958-\u0961])",
            r"\g<1>" + "\u094d" + r"\g<2>",
            text,
        )
        # 4. Unicode NFC normalization
        text = unicodedata.normalize("NFC", text)
    return text


def reconstruct_reading_order_and_tables(
    raw_lines: list[Any],
) -> tuple[str, list[dict], list[Any]]:
    """Geometrically sort OCR detections into human reading order and detect table structures.

    - Clusters boxes into visual lines and table rows using vertical alignment.
    - Sorts boxes horizontally within lines (left-to-right).
    - Preserves row and column boundaries, separating distinct column cells with ' | '.
    - Keeps table headings, descriptions, quantities, rates, amounts, and totals in natural order.
    - Returns (reconstructed_text, structured_tables, sorted_line_items).
    """
    if not raw_lines:
        return "", [], []

    parsed: list[dict] = []
    for item in raw_lines:
        box = None
        text = ""
        score = 0.0
        if isinstance(item, dict):
            box = item.get("box")
            text = str(item.get("text", ""))
            score = float(item.get("score", 0.9))
        elif isinstance(item, (list, tuple)):
            if len(item) >= 2:
                box = item[0]
                val = item[1]
                if isinstance(val, (list, tuple)) and len(val) >= 2:
                    text = str(val[0])
                    try:
                        score = float(val[1])
                    except (ValueError, TypeError):
                        score = 0.0
                else:
                    text = str(val)
                    if len(item) >= 3:
                        try:
                            score = float(item[2])
                        except (ValueError, TypeError):
                            score = 0.0
        t_clean = text.strip()
        if not t_clean:
            continue

        x0, y0, x1, y1 = 0.0, 0.0, 0.0, 0.0
        valid_box = False
        if box is not None:
            try:
                pts = np.array(box, dtype=float).reshape(-1, 2)
                x0 = float(pts[:, 0].min())
                y0 = float(pts[:, 1].min())
                x1 = float(pts[:, 0].max())
                y1 = float(pts[:, 1].max())
                valid_box = True
            except Exception:
                pass

        parsed.append({
            "box": box,
            "text": t_clean,
            "score": score,
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "cx": (x0 + x1) / 2.0,
            "cy": (y0 + y1) / 2.0,
            "w": max(1.0, x1 - x0),
            "h": max(1.0, y1 - y0),
            "valid": valid_box,
        })

    if not parsed:
        return "", [], []

    valid_items = [p for p in parsed if p["valid"]]
    if len(valid_items) < 2:
        seq_text = "\n".join(p["text"] for p in parsed)
        return seq_text, [], [(p["box"], p["text"], p["score"]) for p in parsed]

    heights = [p["h"] for p in valid_items]
    med_h = float(np.median(heights)) if heights else 20.0
    row_tol = max(6.0, 0.45 * med_h)

    sorted_by_y = sorted(valid_items, key=lambda b: (b["cy"], b["x0"]))

    rows: list[list[dict]] = []
    current_row: list[dict] = []
    current_cy: float | None = None

    for b in sorted_by_y:
        if current_cy is None or abs(b["cy"] - current_cy) <= row_tol:
            current_row.append(b)
            current_cy = sum(item["cy"] for item in current_row) / len(current_row)
        else:
            rows.append(current_row)
            current_row = [b]
            current_cy = b["cy"]
    if current_row:
        rows.append(current_row)

    rows.sort(key=lambda r: min(b["y0"] for b in r))

    output_lines: list[str] = []
    structured_tables: list[dict] = []
    sorted_items_out: list[Any] = []

    current_table_rows: list[list[str]] = []
    current_table_headers: list[str] = []

    for r in rows:
        r.sort(key=lambda b: b["x0"])
        for b in r:
            sorted_items_out.append((b["box"], b["text"], b["score"]))

        if len(r) == 1:
            single_text = normalize_devanagari_text(r[0]["text"].strip())
            # Check if this single item is a continuation of the previous table row's description
            is_continuation = False
            if current_table_headers:
                # Disallow continuation if the text indicates a total/summary line or section
                if not re.match(
                    r"^(total|subtotal|grand total|tax|vat|gst|amount due|balance|authorized|signature|note|terms)\b",
                    single_text,
                    re.I,
                ):
                    target_row = current_table_rows[-1] if current_table_rows else current_table_headers
                    # Identify description cell index: usually index 1 if index 0 is Sr/item no, otherwise index 0
                    desc_idx = 1 if len(target_row) > 1 and target_row[0].strip().isdigit() else 0
                    if desc_idx < len(target_row):
                        target_row[desc_idx] = f"{target_row[desc_idx]} {single_text}".strip()
                        if output_lines:
                            output_lines[-1] = " | ".join(c.strip() for c in target_row if c.strip())
                        is_continuation = True

            if not is_continuation:
                output_lines.append(single_text)
                if len(current_table_rows) >= 2:
                    structured_tables.append({
                        "headers": current_table_headers,
                        "rows": current_table_rows,
                    })
                current_table_rows = []
                current_table_headers = []
        else:
            cells: list[str] = []
            current_cell_words: list[str] = [r[0]["text"]]

            for i in range(len(r) - 1):
                b_curr = r[i]
                b_next = r[i + 1]
                gap = b_next["x0"] - b_curr["x1"]
                if gap > max(16.0, 1.1 * med_h):
                    cells.append(" ".join(current_cell_words))
                    current_cell_words = [b_next["text"]]
                else:
                    current_cell_words.append(b_next["text"])
            cells.append(" ".join(current_cell_words))
            cells = [normalize_devanagari_text(c.strip()) for c in cells if c.strip()]

            if len(cells) > 1:
                row_str = " | ".join(cells)
                output_lines.append(row_str)
                if not current_table_headers:
                    current_table_headers = cells
                elif (
                    len(current_table_rows) == 0
                    and len(cells) == len(current_table_headers)
                    and not any(re.search(r"\d", c) for c in cells)
                ):
                    # Multi-line header detected: merge second header row into headers
                    current_table_headers = [
                        f"{h} {c}".strip() for h, c in zip(current_table_headers, cells)
                    ]
                    if len(output_lines) >= 2:
                        output_lines.pop()
                        output_lines[-1] = " | ".join(current_table_headers)
                else:
                    current_table_rows.append(cells)
            elif cells:
                output_lines.append(cells[0])
                if len(current_table_rows) >= 2:
                    structured_tables.append({
                        "headers": current_table_headers,
                        "rows": current_table_rows,
                    })
                current_table_rows = []
                current_table_headers = []

    if len(current_table_rows) >= 1 and current_table_headers:
        structured_tables.append({
            "headers": current_table_headers,
            "rows": current_table_rows,
        })

    full_text = "\n".join(output_lines)
    return full_text, structured_tables, sorted_items_out


def clean_extracted_text(text: str) -> str:
    """
    Clean OCR and PDF text noise while preserving meaningful structure.
    - Preserves Devanagari script (\\u0900-\\u097F), Latin text, digits, punctuation.
    - Recombines split Devanagari vowel signs (matras), virama, and nukta.
    - Removes non-printable control characters (except newline, tab).
    - Replaces weird whitespace and repeated symbols.
    - Normalizes consecutive blank lines.
    """
    if not text:
        return ""

    # Remove non-printable control characters, preserving all printable Unicode (including Devanagari)
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in ("\n", "\r", "\t"))

    # Normalize carriage returns
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")

    # Recombine split Devanagari matras, conjuncts, and hyphenated line-end splits
    cleaned = normalize_devanagari_text(cleaned)

    # Replace multiple spaces/tabs with single space on the same line
    cleaned = re.sub(r"[ \t]+", " ", cleaned)

    # Normalize more than 2 consecutive newlines to 2 newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Strip leading and trailing whitespace per line
    lines = [line.strip() for line in cleaned.split("\n")]
    cleaned = "\n".join(lines).strip()

    return cleaned


def detect_language(text: str) -> tuple[str, float, dict[str, Any]]:
    """
    Automatic two-stage language detector:
    Stage 1: Script detection (Latin vs Devanagari vs Mixed)
    Stage 2: Indic linguistic classification (Hindi vs Marathi vs Uncertain)

    Returns:
        tuple[detected_code, confidence_score, details_dict]
        detected_code can be: 'en', 'hi', 'mr', 'en+hi', 'en+mr', 'uncertain'
    """
    if not text or not text.strip():
        return "en", 1.0, {"reason": "empty_text", "script": "none"}

    # Count script letters
    latin_chars = len(re.findall(r"[a-zA-Z]", text))
    devanagari_chars = len(re.findall(r"[\u0900-\u097F]", text))
    total_letters = latin_chars + devanagari_chars

    if total_letters == 0:
        return "en", 1.0, {"reason": "no_alphabet_chars", "script": "none"}

    latin_ratio = latin_chars / total_letters
    dev_ratio = devanagari_chars / total_letters

    has_latin = latin_chars >= 3 and latin_ratio >= 0.08
    has_devanagari = devanagari_chars >= 3 and dev_ratio >= 0.08

    # Case 1: Pure Latin Script
    if has_latin and not has_devanagari:
        return "en", round(latin_ratio, 2), {
            "script": "latin",
            "latin_ratio": latin_ratio,
            "dev_ratio": dev_ratio,
        }

    # Case 2: Devanagari present (Pure or Mixed) -> Stage 2 Linguistic Classifier
    devanagari_words = re.findall(r"[\u0900-\u097F]+", text)

    marathi_score = 0.0
    hindi_score = 0.0
    matched_mr: list[str] = []
    matched_hi: list[str] = []

    # 1. Distinctive character matching
    for ch in text:
        if ch in MARATHI_DISTINCTIVE_CHARS:
            marathi_score += 3.0
            matched_mr.append(f"char:{ch}")

    # 2. Lexical word matching
    for w in devanagari_words:
        if w in MARATHI_WORDS:
            marathi_score += 1.0
            matched_mr.append(w)
        if w in HINDI_WORDS:
            hindi_score += 1.0
            matched_hi.append(w)

    total_score = marathi_score + hindi_score

    if total_score == 0.0:
        regional_lang = "uncertain"
        lang_conf = 0.50
    elif marathi_score > hindi_score:
        regional_lang = "mr"
        lang_conf = round(marathi_score / total_score, 2)
    elif hindi_score > marathi_score:
        regional_lang = "hi"
        lang_conf = round(hindi_score / total_score, 2)
    else:
        regional_lang = "uncertain"
        lang_conf = 0.50

    # 3. Combine script mixture
    if has_latin and has_devanagari:
        final_lang = f"en+{regional_lang}" if regional_lang != "uncertain" else "en+hi"
        script_type = "mixed"
    elif has_devanagari:
        final_lang = regional_lang if regional_lang != "uncertain" else "hi"
        script_type = "devanagari"
    else:
        final_lang = "en"
        script_type = "latin"

    return final_lang, lang_conf, {
        "script": script_type,
        "regional": regional_lang,
        "marathi_score": marathi_score,
        "hindi_score": hindi_score,
        "matched_mr": matched_mr,
        "matched_hi": matched_hi,
        "latin_ratio": latin_ratio,
        "dev_ratio": dev_ratio,
    }


def _estimate_ink_ratio(image: Image.Image) -> float:
    """Fraction of dark pixels on a downscaled grayscale copy (blank-page detector).

    Calibrated so a page with even one short word (~0.001 measured) is NOT blank;
    only essentially pure-white renders count as blank.

    The measurement is taken on the page's own contrast range: an aged, browned
    or washed-out scan keeps its glyphs in a narrow band far above the fixed cut,
    and judging "dark" against that cut used to declare such pages blank, which
    silently dropped every word on them.  A frame with no usable range at all
    (scanner lid, uniform tint) stays blank.
    """
    try:
        small = image.convert("L")
        small.thumbnail((400, 400))
        arr = np.asarray(small).astype(np.int16)
        low, high = np.percentile(arr, 1), np.percentile(arr, 99)
        if high - low >= 24:
            arr = np.clip((arr - low) * 255.0 / (high - low), 0, 255)
        return float((arr < 160).mean())
    except Exception:
        return 1.0


def _boxes_imply_vertical_text(ocr_lines: list | None) -> bool:
    """True when the recognised boxes are tall and narrow, i.e. the text runs
    across the page vertically.

    A page scanned through 90 degrees still OCRs "successfully": the recogniser
    returns confident-looking junk, so neither a short result nor a low score
    reveals the rotation.  Geometry does - horizontal words are wide, rotated
    ones are tall - and the caller then spends the existing rotation retry.
    """
    try:
        aspects = []
        for entry in ocr_lines or []:
            box = entry[0] if isinstance(entry, (tuple, list)) else entry
            pts = np.asarray(box, dtype=np.float32).reshape(-1, 2)
            if pts.shape[0] < 2:
                continue
            width = float(pts[:, 0].max() - pts[:, 0].min())
            height = float(pts[:, 1].max() - pts[:, 1].min())
            if width <= 0 or height <= 0:
                continue
            aspects.append(height / width)
        if len(aspects) < 6:
            return False
        vertical = sum(1 for a in aspects if a > 1.6)
        return vertical / len(aspects) >= 0.7
    except Exception:
        return False


def preprocess_scan_image(image: Image.Image) -> Image.Image | None:
    """Conservative escalation preprocessing for low-quality scanned pages.

    Grayscale + gentle contrast/sharpen + small-angle deskew. Never binarizes, so
    thin handwriting, stamps and table lines survive. Returns None if nothing
    applicable could be computed.
    """
    try:
        img = image.convert("L")

        # Gentle deskew via OpenCV minAreaRect on foreground pixels
        try:
            import cv2
            import numpy as _np

            arr = _np.array(img)
            # Downscale for speed on large renders
            scale = 1.0
            if arr.shape[0] > 900:
                scale = 900.0 / arr.shape[0]
                arr = cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            edges = cv2.Canny(arr, 50, 150)
            coords = cv2.findNonZero(edges)
            angle = 0.0
            if coords is not None and len(coords) > 50:
                rect = cv2.minAreaRect(coords)
                a = rect[-1]
                if a < -45:
                    a = 90 + a
                elif a > 45:
                    a = a - 90
                angle = float(a)
            if 0.4 <= abs(angle) <= 10.0:
                img = img.rotate(angle, expand=True, fillcolor=255)
        except Exception as e:
            logger.debug(f"Deskew skipped: {e}")

        from PIL import ImageOps

        img = ImageOps.autocontrast(img, cutoff=1)
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
        return img.convert("RGB")
    except Exception as e:
        logger.warning(f"Scan preprocessing failed: {e}")
        return None


def _perform_image_ocr(
    image: Image.Image, language: str = "auto"
) -> tuple[str, float, str, str, list]:
    """
    Perform OCR on a PIL Image:
    - 'auto': Runs PaddleOCR Multilingual Devanagari model, then automatically classifies language ('en', 'hi', 'mr', 'en+hi', 'en+mr', 'uncertain').
    - 'en': Runs RapidOCR baseline (with Tesseract fallback).
    - 'hi' / 'mr': Runs PaddleOCR Devanagari model with designated regional tag.

    Returns:
        tuple[extracted_text, confidence_percent, engine_name, detected_language, lines]
        where lines is a list of (box, text) for engines that report geometry
        (used by scanned-table cell assignment); empty list when unavailable.
    """
    norm_lang = (language or "auto").lower().strip()

    # 1. Explicit English Request -> RapidOCR Baseline
    if norm_lang == "en":
        rapid_engine = get_rapid_ocr_engine()
        if rapid_engine is not None:
            try:
                img_byte_arr = io.BytesIO()
                image.convert("RGB").save(img_byte_arr, format="PNG")
                img_bytes = img_byte_arr.getvalue()

                ocr_res, _ = rapid_engine(img_bytes)
                if ocr_res:
                    line_items = [(item[0], item[1], float(item[2])) for item in ocr_res if item[1].strip()]
                    raw_text, tables, sorted_lines = reconstruct_reading_order_and_tables(line_items)
                    scores = [l[2] for l in sorted_lines] if sorted_lines else [float(item[2]) for item in ocr_res]
                    avg_confidence = (sum(scores) / len(scores)) if scores else 0.0
                    return raw_text, round(avg_confidence * 100.0, 2), "RapidOCR", "en", sorted_lines
                else:
                    return "", 0.0, "RapidOCR", "en", []
            except Exception as e:
                logger.warning(f"RapidOCR extraction failed: {e}")

        # Tesseract fallback for English
        try:
            import pytesseract

            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            confidences = []
            words = []
            for i, word in enumerate(data.get("text", [])):
                if word.strip():
                    words.append(word)
                    conf = float(data["conf"][i])
                    if conf >= 0:
                        confidences.append(conf)

            full_text = " ".join(words)
            avg_conf = (sum(confidences) / len(confidences)) if confidences else 70.0
            return full_text, round(avg_conf, 2), "Tesseract", "en", []
        except Exception as e:
            logger.warning(f"Tesseract English OCR fallback failed: {e}")

        return "", 0.0, "None", "en", []

    # 2. Automatic Language Detection or Regional Language -> PaddleOCR Multilingual Pipeline
    paddle_engine = get_paddle_devanagari_engine()
    if paddle_engine is not None:
        try:
            img_np = np.array(image.convert("RGB"))
            h, w = img_np.shape[:2]
            pad_h = (32 - (h % 32)) % 32
            pad_w = (32 - (w % 32)) % 32
            if pad_h > 0 or pad_w > 0:
                img_np = np.pad(img_np, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")

            ocr_res = paddle_engine.ocr(img_np, cls=False)

            if ocr_res and ocr_res[0]:
                lines = ocr_res[0]
                line_items = [
                    (line[0], line[1][0], float(line[1][1]))
                    for line in lines
                    if line[1][0].strip()
                ]
                raw_text, tables, sorted_lines = reconstruct_reading_order_and_tables(line_items)
                scores = [l[2] for l in sorted_lines] if sorted_lines else [float(line[1][1]) for line in lines if line[1][0].strip()]

                avg_confidence = (sum(scores) / len(scores)) if scores else 0.0
                conf_pct = round(avg_confidence * 100.0, 2)

                # Automatic Language Detection on the OCR extracted text
                if norm_lang == "auto":
                    detected_lang, _, _ = detect_language(raw_text)
                else:
                    detected_lang = norm_lang

                # Assign descriptive OCR engine name
                if detected_lang == "mr":
                    engine_name = "PaddleOCR-Marathi"
                elif detected_lang == "hi":
                    engine_name = "PaddleOCR-Hindi"
                elif detected_lang == "en+mr":
                    engine_name = "PaddleOCR-Mixed-Marathi"
                elif detected_lang == "en+hi":
                    engine_name = "PaddleOCR-Mixed-Hindi"
                elif detected_lang == "en":
                    engine_name = "PaddleOCR-English"
                else:
                    engine_name = "PaddleOCR-Devanagari"

                return raw_text, conf_pct, engine_name, detected_lang, sorted_lines
            else:
                return "", 0.0, "PaddleOCR-Devanagari", "en", []
        except Exception as e:
            logger.exception(f"PaddleOCR extraction failed: {e}")

    # Fallback to RapidOCR if PaddleOCR fails
    rapid_engine = get_rapid_ocr_engine()
    if rapid_engine is not None:
        try:
            img_byte_arr = io.BytesIO()
            image.convert("RGB").save(img_byte_arr, format="PNG")
            img_bytes = img_byte_arr.getvalue()

            ocr_res, _ = rapid_engine(img_bytes)
            if ocr_res:
                line_items = [(item[0], item[1], float(item[2])) for item in ocr_res if item[1].strip()]
                raw_text, tables, sorted_lines = reconstruct_reading_order_and_tables(line_items)
                scores = [l[2] for l in sorted_lines] if sorted_lines else [float(item[2]) for item in ocr_res]
                avg_confidence = (sum(scores) / len(scores)) if scores else 0.0
                det_lang, _, _ = detect_language(raw_text)
                return raw_text, round(avg_confidence * 100.0, 2), "RapidOCR-Fallback", det_lang, sorted_lines
        except Exception as e:
            logger.warning(f"RapidOCR fallback failed: {e}")

    return "", 0.0, "None", "en", []


def process_image_document(
    file_path: Path, language: str = "auto"
) -> DocumentExtractionResult:
    """
    Process single image documents (.png, .jpg, .jpeg) with automatic language detection,
    rotation/deskew escalation, and geometric table reconstruction.
    """
    try:
        with Image.open(file_path) as img:
            width, height = img.size
            extracted_text, confidence, engine, detected_lang, ocr_lines = (
                _perform_image_ocr(img, language=language)
            )
            # The image the accepted text was read from: escalation can deskew or
            # rotate it, and the table model has to see the same frame.
            ocr_image = img
            # Which quarter turn, if any, the frame the text was read from is
            # rotated by. A turn is invertible, so the cell boxes it produces can
            # be put back on the page the viewer shows.
            read_rotation: int | None = None

            # Escalation on low-quality, vertical or rotated scans
            vertical_boxes = _boxes_imply_vertical_text(ocr_lines)
            unusable = len(clean_extracted_text(extracted_text).strip()) < 10 or confidence < 50.0

            if unusable or vertical_boxes:
                prepared = preprocess_scan_image(img)
                if prepared is not None:
                    alt_text, alt_conf, alt_engine, alt_lang, alt_lines = (
                        _perform_image_ocr(prepared, language=language)
                    )
                    if len(alt_text.strip()) > len(extracted_text.strip()) and alt_conf >= confidence * 0.9:
                        extracted_text, confidence, engine, detected_lang, ocr_lines = (
                            alt_text, alt_conf, f"{alt_engine}-Preprocessed", alt_lang, alt_lines
                        )
                        ocr_image = prepared
                        vertical_boxes = _boxes_imply_vertical_text(ocr_lines)

                if vertical_boxes or len(extracted_text.strip()) < 10:
                    for rot in (90, 180, 270):
                        rot_img = img.rotate(-rot, expand=True, fillcolor=255)
                        alt_text, alt_conf, alt_engine, alt_lang, alt_lines = (
                            _perform_image_ocr(rot_img, language=language)
                        )
                        if len(alt_text.strip()) >= 10 and alt_conf >= 55.0 and not _boxes_imply_vertical_text(alt_lines):
                            extracted_text = alt_text
                            confidence = alt_conf
                            engine = f"{alt_engine}-Rot{rot}"
                            detected_lang = alt_lang
                            ocr_lines = alt_lines
                            ocr_image = rot_img
                            read_rotation = rot
                            break

            # Ruled table extraction if image has table lines
            image_tables: list[Any] = []
            image_table_count = 0
            if _looks_like_ruled_table(ocr_image):
                try:
                    need_deva = detected_lang in ("hi", "mr", "en+hi", "en+mr", "uncertain")
                    image_tables, image_table_count = _extract_scanned_tables(
                        ocr_image,
                        devanagari_ocr=need_deva,
                        ocr_lines=ocr_lines,
                        page_number=1,
                    )
                    _place_grid_on_page(
                        image_tables,
                        read_rotation,
                        ocr_image is not img and read_rotation is None,
                        (width, height),
                    )
                    t_lines = _table_text_lines(
                        image_tables, 1, marker="scanned-table"
                    )
                    if t_lines:
                        t_text = "\n".join(t_lines)
                        extracted_text = f"{extracted_text}\n\n{t_text}".strip()
                except Exception as table_err:
                    logger.warning(
                        f"Table extraction failed for image {file_path}: {table_err}; preserving recognized text"
                    )

            cleaned_text = clean_extracted_text(extracted_text)

            page = ExtractedPage(
                page_number=1,
                text=cleaned_text,
                confidence=confidence,
                detected_language=detected_lang,
                width=width,
                height=height,
                raw_text=extracted_text,
                page_type="scanned",
                engine=engine,
                line_boxes=ocr_lines or [],
                table_count=image_table_count,
                tables=image_tables,
            )

            return DocumentExtractionResult(
                pages=[page],
                combined_text=cleaned_text,
                overall_ocr_confidence=confidence,
                ocr_engine=engine,
                detected_language=detected_lang,
                is_ocr_applied=True,
            )
    except Exception as e:
        logger.error(f"Failed to process image {file_path}: {e}")
        return DocumentExtractionResult(
            pages=[ExtractedPage(page_number=1, text="", confidence=0.0)],
            combined_text="",
            overall_ocr_confidence=0.0,
            ocr_engine="Error",
            detected_language="en",
            is_ocr_applied=True,
        )


KNOWN_STATUS_WORDS = {
    "PAID", "UNPAID", "REFUNDED", "CANCELLED", "CANCELED",
    "VOID", "OVERDUE", "PENDING", "DRAFT", "COMPLETED", "APPROVED", "REJECTED"
}


def _extract_page_tables(
    pdf_page: Any,
) -> tuple[list[Any], list[Any]]:
    """Read ruled/contour tables from a digital PDF page via PyMuPDF.

    Returns (table structures to keep, table bounding rects). Each structure keeps the
    grid coordinate, bounding box, span and header flag of every cell, so the row
    and column associations survive without re-deriving them from text. An empty
    digital cell is stored empty: the text layer is authoritative and nothing is
    invented for it.

    A grid that contradicts itself is not kept as a table: it is replaced by a
    record that says why, and only the accepted grid's rectangle is excluded from
    the page's prose, so the text of a refused table is never lost.
    """
    measured: list[tuple[Any, Any]] = []
    try:
        tables = pdf_page.find_tables()
        try:
            page_words = list(pdf_page.get_text("words"))
        except Exception:
            page_words = []
        for table in tables.tables:
            rows = table.extract()
            if not rows:
                continue
            structure = from_pymupdf_table(pdf_page, table, rows, page_words)
            for cell in structure.cells:
                cell.text = normalize_devanagari_text(cell.text)
            measured.append((structure, table.bbox))
    except Exception as e:
        logger.debug(f"Table extraction skipped on page {getattr(pdf_page, 'number', '?')}: {e}")
        return [], []
    rect = getattr(pdf_page, "rect", None)
    page_size = (
        (float(rect.width), float(rect.height)) if rect is not None else None
    )
    screening = screen_structures(
        [structure for structure, _ in measured],
        page_size=page_size,
        page_lines=len(page_words),
    )
    rects = [
        bbox
        for (structure, bbox), decision in zip(measured, screening.decisions)
        if decision.accept and bbox is not None
    ]
    return screening.kept, rects


def _table_text_lines(
    structures: list[Any], page_number: int, marker: str = "table"
) -> list[str]:
    """Render stored structures into the ' | ' line format the index understands."""
    lines: list[str] = []
    for structure in structures:
        rows = structure.row_texts()
        if not rows:
            continue
        lines.append(f"[{marker} p{page_number}]")
        lines.extend(rows)
    return lines


def _looks_like_ruled_table(image: Image.Image) -> bool:
    """Cheap morphological check for at least 2 long horizontal + 2 vertical lines."""
    try:
        import cv2

        gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
        h, w = gray.shape
        small = cv2.resize(gray, (max(1, w // 2), max(1, h // 2)))
        _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, bw.shape[1] // 25), 1))
        vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, bw.shape[0] // 25)))
        hl = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
        vl = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
        n_h = cv2.connectedComponents(hl)[0] - 1
        n_v = cv2.connectedComponents(vl)[0] - 1
        return n_h >= 2 and n_v >= 2
    except Exception:
        return False


_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def _line_positions(
    mask: Any, *, horizontal: bool, min_length: float
) -> list[tuple[float, float, float, float]]:
    """Long straight runs in a morphology mask, as (across, start, end, thickness).

    `across` is the coordinate of the line itself (its y for a horizontal rule, its
    x for a vertical one) and `start`/`end` the span it covers.
    """
    import cv2

    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8
    )
    found: list[tuple[float, float, float, float]] = []
    for index in range(1, count):
        x, y, width, height = (
            float(stats[index, cv2.CC_STAT_LEFT]),
            float(stats[index, cv2.CC_STAT_TOP]),
            float(stats[index, cv2.CC_STAT_WIDTH]),
            float(stats[index, cv2.CC_STAT_HEIGHT]),
        )
        if horizontal:
            if width >= min_length:
                found.append((y + height / 2.0, x, x + width, height))
        elif height >= min_length:
            found.append((x + width / 2.0, y, y + height, width))
    return found


def _cluster_positions(values: list[float], tolerance: float) -> list[float]:
    """Collapse line coordinates that are really the same rule into one position."""
    clusters: list[list[float]] = []  # [running mean, count]
    for value in sorted(values):
        if clusters and value - clusters[-1][0] <= tolerance:
            count = clusters[-1][1] + 1
            clusters[-1] = [(clusters[-1][0] * (count - 1) + value) / count, count]
        else:
            clusters.append([value, 1])
    return [mean for mean, _count in clusters]


def _strip_coverage(
    mask: Any, *, horizontal: bool, at: float, start: float, end: float
) -> float:
    """How much of a divider between two cells is actually drawn."""
    import cv2

    if end - start < 4.0:
        return 1.0
    band = 3
    if horizontal:
        strip = mask[max(0, int(at) - band) : int(at) + band + 1, int(start) : int(end)]
        if strip.size == 0:
            return 0.0
        return float(np.mean(strip.max(axis=0) > 0))
    strip = mask[int(start) : int(end), max(0, int(at) - band) : int(at) + band + 1]
    if strip.size == 0:
        return 0.0
    return float(np.mean(strip.max(axis=1) > 0))


def _ruling_regions(image: Image.Image) -> list[dict]:
    """Measure every ruled table on a page image: bounds, cells and merges.

    Each region carries the row and column line positions, one rectangle per cell
    and the colspan/rowspan a *missing* divider proves. The ruling is measured from
    the page itself, so a cell's row and column come from where its pixels are
    rather than from a recognition model's guess about them.
    """
    try:
        import cv2

        gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    except Exception:
        return []
    height, width = gray.shape
    if height < 120 or width < 120:
        return []
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    h_mask = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, width // 8), 1)),
    )
    v_mask = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 10))),
    )
    try:
        rules = _line_positions(h_mask, horizontal=True, min_length=0.30 * width)
        uprights = _line_positions(v_mask, horizontal=False, min_length=0.06 * height)
    except Exception:
        return []
    if len(rules) < 3 or len(uprights) < 3:
        return []

    rule_ys = _cluster_positions([r[0] for r in rules], tolerance=6.0)
    if len(rule_ys) < 3:
        return []
    pitches = sorted(
        later - earlier
        for earlier, later in zip(rule_ys, rule_ys[1:])
        if later - earlier > 3.0
    )
    pitch = pitches[len(pitches) // 2] if pitches else 0.0
    if pitch <= 0:
        return []

    regions: list[dict] = []
    # A gap far larger than the row pitch is a different table, not a row of this one.
    groups: list[list[float]] = [[rule_ys[0]]]
    for y, previous in zip(rule_ys[1:], rule_ys):
        if y - previous > 2.5 * pitch:
            groups.append([y])
        else:
            groups[-1].append(y)

    thicknesses = sorted(r[3] for r in rules)
    median_thickness = thicknesses[len(thicknesses) // 2] or 1.0
    for ys in groups:
        if len(ys) < 3:
            continue
        top, bottom = ys[0], ys[-1]
        span = bottom - top
        columns = [
            u
            for u in uprights
            if u[1] <= top + 0.25 * span and u[2] >= bottom - 0.25 * span
        ]
        col_xs = _cluster_positions([u[0] for u in columns], tolerance=6.0)
        if len(col_xs) < 3:
            continue
        rows = len(ys) - 1
        cols = len(col_xs) - 1
        v_present = [
            [
                _strip_coverage(
                    v_mask,
                    horizontal=False,
                    at=col_xs[c],
                    start=ys[r] + 4,
                    end=ys[r + 1] - 4,
                )
                >= 0.55
                for c in range(len(col_xs))
            ]
            for r in range(rows)
        ]
        h_present = [
            [
                _strip_coverage(
                    h_mask,
                    horizontal=True,
                    at=ys[r],
                    start=col_xs[c] + 4,
                    end=col_xs[c + 1] - 4,
                )
                >= 0.55
                for c in range(cols)
            ]
            for r in range(len(ys))
        ]

        occupied: set[tuple[int, int]] = set()
        cells: list[dict] = []
        for r in range(rows):
            for c in range(cols):
                if (r, c) in occupied:
                    continue
                right = c
                while (
                    right + 1 < cols
                    and not v_present[r][right + 1]
                    and (r, right + 1) not in occupied
                ):
                    right += 1
                lower = r
                while (
                    lower + 1 < rows
                    and all(not h_present[lower + 1][k] for k in range(c, right + 1))
                    and all((lower + 1, k) not in occupied for k in range(c, right + 1))
                ):
                    lower += 1
                for rr in range(r, lower + 1):
                    for cc in range(c, right + 1):
                        occupied.add((rr, cc))
                cells.append(
                    {
                        "row": r,
                        "col": c,
                        "colspan": right - c + 1,
                        "rowspan": lower - r + 1,
                        "rect": (
                            float(col_xs[c]),
                            float(ys[r]),
                            float(col_xs[right + 1]),
                            float(ys[lower + 1]),
                        ),
                    }
                )

        emphasised = h_present and _strip_coverage(
            h_mask,
            horizontal=True,
            at=ys[1],
            start=col_xs[0] + 4,
            end=col_xs[-1] - 4,
        ) >= 0.55
        under_header = [r for r in rules if abs(r[0] - ys[1]) <= 6.0]
        thicker = bool(
            under_header and under_header[0][3] >= 1.4 * median_thickness
        )
        note = None
        if not thicker:
            note = "header row inferred from being the top band of the ruling"
        regions.append(
            {
                "row_bounds": [float(y) for y in ys],
                "col_bounds": [float(x) for x in col_xs],
                "cells": cells,
                "header_rows": [0],
                "bbox": (
                    float(col_xs[0]),
                    float(top),
                    float(col_xs[-1]),
                    float(bottom),
                ),
                "note": note,
                "emphasised_rule": emphasised,
            }
        )
    return regions


def _group_rects_by_html_rows(
    rects: list[tuple[float, float, float, float]],
    rows: list[list[tuple[str, int, int, bool]]],
) -> list[list[tuple[float, float, float, float]]] | None:
    """Pair every HTML row with exactly its own cell boxes.

    PP-Structure emits cell_bbox in the same row-major order as the table HTML it
    generated for the same page, so a row's expanded colspan count selects its own
    boxes. Splitting the boxes by vertical gaps instead banding them disagrees with
    the model on how many rows there are (52 model rows vs 33 gap bands measured on
    a Marathi page), and the disagreement used to discard every cell assignment.
    Returns None unless the counts add up and the paired rows descend the page.
    """
    counts = [sum(max(1, cell[1]) for cell in row) for row in rows]
    if not counts or sum(counts) != len(rects):
        return None
    grouped: list[list[tuple[float, float, float, float]]] = []
    index = 0
    for count in counts:
        grouped.append(rects[index:index + count])
        index += count
    centers = [
        sum((r[1] + r[3]) / 2.0 for r in group) / len(group)
        for group in grouped
        if group
    ]
    if len(centers) != len(grouped) or any(
        later < earlier for earlier, later in zip(centers, centers[1:])
    ):
        return None
    return grouped


def _fit_rects_into_page(
    rects: list[tuple[float, float, float, float]],
    table_bbox: Any,
    image: Image.Image,
) -> list[tuple[float, float, float, float]]:
    """Map the model's cell cloud onto the region of the page it covers.

    The boxes come back in the table model's resized frame and can run past the
    page edge (observed x 2154 on a 1653px page), where they would match no OCR
    line at all. Scaling the cloud into the table's own image-space bbox keeps the
    relative layout and makes the boxes comparable with the page OCR coordinates.
    """
    if not rects:
        return rects
    xs = [value for rect in rects for value in (rect[0], rect[2])]
    ys = [value for rect in rects for value in (rect[1], rect[3])]
    cloud_x0, cloud_x1, cloud_y0, cloud_y1 = min(xs), max(xs), min(ys), max(ys)
    already_in_page = (
        cloud_x0 >= 0
        and cloud_y0 >= 0
        and cloud_x1 <= image.width
        and cloud_y1 <= image.height
    )
    if already_in_page:
        return rects

    try:
        box = [float(value) for value in table_bbox][:4]
        target_x0, target_y0, target_x1, target_y1 = box
    except (TypeError, ValueError, IndexError):
        target_x0 = target_y0 = 0.0
        target_x1, target_y1 = float(image.width), float(image.height)
    target_x0 = max(0.0, min(target_x0, image.width - 1.0))
    target_y0 = max(0.0, min(target_y0, image.height - 1.0))
    target_x1 = min(float(image.width), max(target_x1, target_x0 + 1.0))
    target_y1 = min(float(image.height), max(target_y1, target_y0 + 1.0))

    span_x = max(1e-6, cloud_x1 - cloud_x0)
    span_y = max(1e-6, cloud_y1 - cloud_y0)
    scale_x = (target_x1 - target_x0) / span_x
    scale_y = (target_y1 - target_y0) / span_y
    return [
        (
            target_x0 + (rect[0] - cloud_x0) * scale_x,
            target_y0 + (rect[1] - cloud_y0) * scale_y,
            target_x0 + (rect[2] - cloud_x0) * scale_x,
            target_y0 + (rect[3] - cloud_y0) * scale_y,
        )
        for rect in rects
    ]


def _as_percent_confidence(score: Any) -> float | None:
    """Normalise an engine score onto a 0-100 percentage.

    Paddle and Rapid report 0..1 while some fallback paths already report per
    cent. A value outside both ranges is not a measurement, so it stays None
    rather than becoming a fabricated confidence.
    """
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if 0.0 <= value <= 1.0:
        return round(value * 100.0, 2)
    if 1.0 < value <= 100.0:
        return round(value, 2)
    return None


def _ocr_line_items(
    lines_src: Any,
) -> list[tuple[float, float, str, float | None, tuple[float, float, float, float]]]:
    """Normalise an engine's line list into (centre-x, centre-y, text, score, box).

    The engines do not agree on shape: Paddle returns (box, (text, score)),
    Rapid returns (box, text, score) and the reading-order helper returns dicts.
    A line whose box cannot be read is dropped rather than placed by guesswork.
    The box is kept as well as its centre because a line that reaches across a
    column divider cannot be attributed to one cell.
    """
    items: list[
        tuple[float, float, str, float | None, tuple[float, float, float, float]]
    ] = []
    for item in lines_src or []:
        box: Any = None
        text = ""
        score: Any = None
        if isinstance(item, dict):
            box = item.get("box")
            text = str(item.get("text", ""))
            score = item.get("score", item.get("confidence"))
        elif isinstance(item, (list, tuple)) and item:
            box = item[0]
            val = item[1] if len(item) >= 2 else None
            if isinstance(val, (list, tuple)) and val:
                text = str(val[0])
                if len(val) >= 2:
                    score = val[1]
            else:
                text = "" if val is None else str(val)
            if len(item) >= 3:
                score = item[2]
        else:
            continue

        if box is None or not text.strip():
            continue
        try:
            pts = np.array(box, dtype=np.float32).reshape(-1, 2)
        except (TypeError, ValueError):
            continue
        items.append(
            (
                float(pts[:, 0].mean()),
                float(pts[:, 1].mean()),
                normalize_devanagari_text(text),
                _as_percent_confidence(score),
                (
                    float(pts[:, 0].min()),
                    float(pts[:, 1].min()),
                    float(pts[:, 0].max()),
                    float(pts[:, 1].max()),
                ),
            )
        )
    return items


# A detection quad is loose: it routinely overshoots the ink it read. The slop a
# line may show before it is called a straddle scales with the height of that
# line - how far a box misses its glyphs has nothing to do with how wide the
# string is - so a line that really reaches into the next column still counts as
# reaching, however long it is.
LINE_FIT_X = 0.15
LINE_FIT_Y = 0.5


def _line_fits_cell(
    box: tuple[float, float, float, float],
    rect: tuple[float, float, float, float],
) -> bool:
    """True when the whole of one recognised line lies inside one cell box."""
    x1, y1, x2, y2 = rect
    bx1, by1, bx2, by2 = box
    grow_x = max(6.0, LINE_FIT_X * (by2 - by1))
    grow_y = max(6.0, LINE_FIT_Y * (by2 - by1))
    return (
        x1 - grow_x <= bx1
        and bx2 <= x2 + grow_x
        and y1 - grow_y <= by1
        and by2 <= y2 + grow_y
    )


def _cells_overlapping(
    box: tuple[float, float, float, float],
    rects: list[tuple[float, float, float, float]],
) -> list[int]:
    bx1, by1, bx2, by2 = box
    return [
        index
        for index, (x1, y1, x2, y2) in enumerate(rects)
        if bx1 < x2 and bx2 > x1 and by1 < y2 and by2 > y1
    ]



def _parse_html_grid(html_str: str) -> list[list[tuple[str, int, int, bool]]]:
    """Turn the table model's HTML into (text, colspan, rowspan, is_header) rows."""
    rows: list[list[tuple[str, int, int, bool]]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html_str, re.S | re.I):
        cells: list[tuple[str, int, int, bool]] = []
        for match in re.finditer(
            r"<(td|th)\b([^>]*)>(.*?)</(?:td|th)>", tr, re.S | re.I
        ):
            tag, attrs, inner = match.group(1), match.group(2), match.group(3)
            text = html_module.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
            col = re.search(r"colspan\s*=\s*[\"']?(\d+)", attrs, re.I)
            rowspan = re.search(r"rowspan\s*=\s*[\"']?(\d+)", attrs, re.I)
            cells.append(
                (
                    text,
                    int(col.group(1)) if col else 1,
                    int(rowspan.group(1)) if rowspan else 1,
                    tag.lower() == "th",
                )
            )
        if cells:
            rows.append(cells)
    return rows


def _rect_from_box(box: Any) -> tuple[float, float, float, float] | None:
    """Normalise either box shape the model returns into one page rectangle."""
    try:
        flat = [float(value) for value in box]
        if len(flat) == 4:
            return (flat[0], flat[1], flat[2], flat[3])
    except (TypeError, ValueError):
        pass
    try:
        pts = np.array(box, dtype=np.float32).reshape(-1, 2)
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        return (float(x0), float(y0), float(x1), float(y1))
    except (TypeError, ValueError):
        return None


def _band_rects_by_gap(
    rects: list[tuple[float, float, float, float]],
) -> list[list[tuple[float, float, float, float]]]:
    """Split cell boxes into visual rows where a vertical gap opens up."""
    heights = sorted(r[3] - r[1] for r in rects)
    med_h = heights[len(heights) // 2] or 20.0
    banded: list[list[tuple[float, float, float, float]]] = []
    current: list[tuple[float, float, float, float]] = []
    last_y: float | None = None
    for rect in sorted(rects, key=lambda b: ((b[1] + b[3]) / 2.0, b[0])):
        yc = (rect[1] + rect[3]) / 2.0
        if last_y is None or yc - last_y <= 0.35 * med_h:
            current.append(rect)
        else:
            banded.append(current)
            current = [rect]
        last_y = yc
    if current:
        banded.append(current)
    for row in banded:
        row.sort(key=lambda b: b[0])
    return banded


def _assign_lines_to_cells(
    page_items: list[Any],
    rows_of_rects: list[list[tuple[float, float, float, float] | None]],
) -> tuple[dict[tuple[int, int], tuple[str, float | None]], set[tuple[int, int]]]:
    """Give every cell box the page lines that lie wholly inside it.

    Returns the per-cell readings plus the cells a line reached across. A slot with
    no box of its own (`None`, the grid position a merge swallowed) can collect
    nothing. A line that stays inside one box is that cell's text; a line whose box
    reaches over a divider into a second cell belongs to neither, so its text is
    not put in either and both are reported as crossed - the page keeps the line in
    its own text and a reviewer sees that something there could not be placed. A
    line matching no box at all is left out rather than guessed into the nearest
    one. The cell's confidence is the mean of the recogniser scores that produced
    it.
    """
    per_cell: dict[tuple[int, int], list[tuple[float, float, str, float | None]]] = {}
    crossed: set[tuple[int, int]] = set()
    rect_of: dict[tuple[int, int], tuple[float, float, float, float]] = {
        (ri, ci): rect
        for ri, row_rects in enumerate(rows_of_rects)
        for ci, rect in enumerate(row_rects)
        if rect is not None
    }
    keys = list(rect_of)
    boxes = [rect_of[key] for key in keys]
    for cx, cy, text, score, box in page_items:
        homes = [key for key in keys if _line_fits_cell(box, rect_of[key])]
        if len(homes) == 1:
            per_cell.setdefault(homes[0], []).append((cy, cx, text, score))
            continue
        if len(homes) > 1:
            # Fits inside several boxes at once (a merge and the slot it covers):
            # the smallest of them is the cell that actually holds it.
            smallest = min(homes, key=lambda key: _rect_area(rect_of[key]))
            per_cell.setdefault(smallest, []).append((cy, cx, text, score))
            continue
        touched = [keys[i] for i in _cells_overlapping(box, boxes)]
        if len(touched) == 1:
            # Sloppy quad, one cell: the line still reaches no divider, so the cell
            # keeps it rather than losing text the page did read.
            per_cell.setdefault(touched[0], []).append((cy, cx, text, score))
            continue
        if len(touched) > 1:
            crossed.update(touched)
            # The part of the line inside each cell is still the best evidence that
            # cell has, but keeping it would mean keeping the other cell's glyphs
            # with it, so neither keeps anything and both are flagged.

    readings: dict[tuple[int, int], tuple[str, float | None]] = {}
    for key, found in per_cell.items():
        ordered = sorted(found, key=lambda v: (round(v[0]), v[1]))
        joined = normalize_devanagari_text(
            " ".join(text for _, _, text, _ in ordered).strip()
        )
        if not joined:
            continue
        scores = [score for _, _, _, score in ordered if score is not None]
        readings[key] = (
            joined,
            round(sum(scores) / len(scores), 2) if scores else None,
        )
    return readings, crossed


def _structure_from_paddle_table(
    table: dict,
    page_items: list[Any],
    script_page: bool,
    image: Image.Image,
    page_number: int,
) -> TableStructure | None:
    """Build one cell-accurate structure from a single PP-Structure table result."""
    res = table.get("res") or {}
    html_raw = res.get("html") or ""
    if isinstance(html_raw, dict):
        html_raw = html_raw.get("table", "")
    rows = _parse_html_grid(str(html_raw))
    if not rows:
        return None

    rects: list[tuple[float, float, float, float]] = []
    for box in res.get("cell_bbox") or res.get("cell_box_list") or res.get("boxes") or []:
        rect = _rect_from_box(box)
        if rect is not None:
            rects.append(rect)

    # Cell boxes come back in the table model's own resized frame and can overshoot
    # the page; fit the whole cloud onto the region of the page the table covers
    # (scaling row by row would distort it) so cell assignment compares against real
    # page coordinates.
    if rects:
        rects = _fit_rects_into_page(rects, table.get("bbox"), image)
    rows_of_rects = _group_rects_by_html_rows(rects, rows) if rects else None
    if rects and rows_of_rects is None:
        banded = _band_rects_by_gap(rects)
        if len(banded) == len(rows):
            rows_of_rects = banded
    note = None
    if rows_of_rects is None:
        note = (
            "the table model returned no cell geometry"
            if not rects
            else "cell boxes did not band into the model's rows"
        )
        note += "; grid taken from the model's HTML alone"

    readings, crossed = (
        _assign_lines_to_cells(page_items, rows_of_rects)
        if rows_of_rects
        else ({}, set())
    )
    model_slot_text: dict[tuple[int, int], str] = {}
    for row_index, html_row in enumerate(rows):
        index = 0
        for text, colspan, _, _ in html_row:
            if text:
                model_slot_text[(row_index, index)] = text
            index += max(1, colspan)
    # Where both readers saw digits in the same cell, the model's printing is the
    # cleaner of the two - but only when the two agree. Measured on a Marathi scan
    # the model reported the row's amount for its rate cell; that disagreement must
    # not overwrite the number the page itself read, so the page keeps its own
    # reading and the cell is flagged for review.
    disputed: set[tuple[int, int]] = set()
    for key in list(readings):
        model_text = model_slot_text.get(key, "")
        if not (model_text and NUMERIC_CELL_RE.fullmatch(model_text)):
            continue
        page_text, score = readings[key]
        if page_text.replace(" ", "") == model_text.replace(" ", ""):
            readings[key] = (model_text, score)
        else:
            disputed.add(key)

    structure = from_paddle_html_rows(
        page_number,
        rows,
        rows_of_rects,
        readings,
        script_page=script_page,
        engine="PP-Structure",
        note=note,
    )
    _flag_cells(
        crossed,
        structure,
        "cell(s) share a recognised line that crosses a cell boundary",
    )
    _flag_cells(
        disputed,
        structure,
        "cell(s) where the page's own number and the table model's do not agree",
    )
    return structure


def _rect_area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _flag_cells(
    keys: set[tuple[int, int]], structure: TableStructure, reason: str
) -> int:
    """Mark the given cells for review, without inventing or replacing their text.

    A straddling line leaves the grid unable to say which cell each glyph belonged
    to, and a number the two readers disagree about leaves it unable to say which
    number is on the page. Neither is a reason to pick one silently: the text the
    page read stays, the cell is flagged, and the reason is written into the note.
    """
    touched = 0
    for cell in structure.cells:
        if (cell.row, cell.col) in keys:
            cell.uncertain = True
            touched += 1
    if touched:
        # The reason is recorded even where the cell was already under review for
        # something else: a number two readers disagree about is a separate fact
        # from a line that straddles a divider, and a reviewer needs both.
        structure.refresh_flags()
        structure.requires_review = True
        structure.add_note(f"{touched} {reason}")
    return touched


def _row_rects_for_grid(region: dict) -> list[list[tuple[float, float, float, float] | None]]:
    """One rectangle per grid position, `None` where a merge swallowed the slot."""
    rows = max(0, len(region["row_bounds"]) - 1)
    cols = max(0, len(region["col_bounds"]) - 1)
    grid: list[list[tuple[float, float, float, float] | None]] = [
        [None] * cols for _ in range(rows)
    ]
    for cell in region["cells"]:
        row, col = int(cell["row"]), int(cell["col"])
        if 0 <= row < rows and 0 <= col < cols:
            grid[row][col] = cell["rect"]
    return grid


def _model_hints_for_grid(
    tables: list[dict], row_count: int, col_count: int
) -> dict[tuple[int, int], str]:
    """The table model's own cell text, but only when its grid is the same shape.

    The model often reports a different number of rows or columns than the ruling
    actually has; mapping its cells onto a grid of another shape would put one
    cell's text inside another, so a mismatch simply gives the hints up.
    """
    for table in tables:
        res = table.get("res") or {}
        html_raw = res.get("html") or ""
        if isinstance(html_raw, dict):
            html_raw = html_raw.get("table", "")
        rows = _parse_html_grid(str(html_raw))
        if len(rows) != row_count:
            continue
        if max((sum(max(1, c) for _, c, _, _ in r) for r in rows), default=0) != col_count:
            continue
        hints: dict[tuple[int, int], str] = {}
        for row_index, html_row in enumerate(rows):
            slot = 0
            for text, colspan, _, _ in html_row:
                if text:
                    hints[(row_index, slot)] = text
                slot += max(1, colspan)
        return hints
    return {}


def _structures_from_ruling(
    image: Image.Image,
    page_items: list[Any],
    script_page: bool,
    tables: list[dict],
    page_number: int,
) -> list[TableStructure]:
    """Build one structure per ruled table, measured from the page's own lines."""
    structures: list[TableStructure] = []
    for region in _ruling_regions(image):
        try:
            rows_of_rects = _row_rects_for_grid(region)
            readings, crossed = _assign_lines_to_cells(page_items, rows_of_rects)
            row_count = len(region["row_bounds"]) - 1
            col_count = len(region["col_bounds"]) - 1
            hints = _model_hints_for_grid(tables, row_count, col_count)
            # Where both readers saw digits in the same cell, the model's printing
            # is the cleaner of the two - but only when the two agree. A number the
            # page read is never replaced by one it did not read.
            disputed: set[tuple[int, int]] = set()
            for key in list(readings):
                hint = hints.get(key, "")
                if not (hint and NUMERIC_CELL_RE.fullmatch(hint)):
                    continue
                page_text, score = readings[key]
                if page_text.replace(" ", "") == hint.replace(" ", ""):
                    readings[key] = (hint, score)
                else:
                    disputed.add(key)
            structure = from_ruled_grid(
                page_number,
                row_bounds=region["row_bounds"],
                col_bounds=region["col_bounds"],
                cells=region["cells"],
                readings=readings,
                script_page=script_page,
                engine="Ruled-grid",
                header_rows=region["header_rows"],
                hints=hints,
                note=region["note"],
            )
            _flag_cells(
                crossed,
                structure,
                "cell(s) share a recognised line that crosses a cell boundary",
            )
            _flag_cells(
                disputed,
                structure,
                "cell(s) where the page's own number and the table model's do not agree",
            )
        except Exception as region_err:
            logger.warning(
                f"Ruled grid skipped on page {page_number}: {region_err}; "
                "keeping the tables that were measured"
            )
            continue
        # A grid with nothing readable in it is still a table the reader found and
        # could not transcribe: keeping it says "review this", dropping it would
        # quietly lose the page's ruled table.
        if structure.has_content() or structure.uncertain_cells:
            structures.append(structure)
    return structures


def _lines_outside_structures(
    lines_src: Any, structures: list[TableStructure]
) -> list[Any]:
    """The recognised lines that no ruled cell took.

    A scanned page's text used to be one geometric reconstruction of every line,
    which put a second, differently grouped copy of each table row beside the
    structure that had just measured it - two versions of the same row that need
    not agree, both indexed. A line a cell holds is rendered from that cell
    instead, so only what is left over goes through the reconstruction. The same
    test `_assign_lines_to_cells` uses decides this: a line that fits one cell, or
    whose loose box reaches only one, belongs to the table, while one that spans a
    divider or lands outside the ruling stays in the page's own text. The untouched
    text every line produced is kept as the page's raw text.
    """
    rects = [
        cell.bbox
        for structure in structures
        for cell in structure.cells
        if cell.bbox is not None
    ]
    source = list(lines_src or [])
    if not rects:
        return source
    kept: list[Any] = []
    for item in source:
        items = _ocr_line_items([item])
        if not items:
            kept.append(item)
            continue
        box = items[0][4]
        fits = any(_line_fits_cell(box, rect) for rect in rects)
        if fits or len(_cells_overlapping(box, rects)) == 1:
            continue
        kept.append(item)
    return kept


def _rotation_box_map(rot: int, page_size: tuple[int, int]):
    """Map a box read from a page turned by `rot` back onto the untouched page.

    `img.rotate(-rot, expand=True)` is a quarter turn, so its inverse is arithmetic
    rather than an approximation: a grid measured after the turn lands on the page
    the viewer shows, instead of pointing at coordinates nothing displays.
    """
    width, height = float(page_size[0]), float(page_size[1])

    def mapped(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = (float(value) for value in box)
        if rot == 90:
            return (y1, height - x2, y2, height - x1)
        if rot == 180:
            return (width - x2, height - y2, width - x1, height - y1)
        if rot == 270:
            return (width - y2, x1, width - y1, x2)
        return (x1, y1, x2, y2)

    return mapped


def _place_grid_on_page(
    structures: list[Any],
    rot: int | None,
    deskewed: bool,
    page_size: tuple[int, int],
) -> None:
    """Put a page's grids into the frame the viewer shows, and say when it cannot.

    A quarter turn inverts exactly. A deskew angle nothing recorded cannot be
    inverted, so its boxes stay where they were measured and the structure reports
    the frame it is in - cells and the lines that filled them share that frame, so
    the grid stays readable, and only an image overlay would need more.
    """
    for structure in structures:
        if rot is not None:
            # A refused record keeps no cells but still carries the area it was
            # measured over; an overlay drawn from it would point elsewhere.
            if structure.cells or structure.bbox is not None:
                structure.remap_boxes(_rotation_box_map(rot, page_size))
        elif deskewed and (structure.cells or structure.bbox is not None):
            structure.frame = "corrected"
            structure.add_note(
                "cell boxes are in the deskewed frame the text was read from"
            )


def _chars_in_region(
    page_items: list[Any], bbox: tuple[float, float, float, float] | None
) -> int:
    """Characters the reader produced inside one grid's own area of the page."""
    if bbox is None:
        return 0
    x1, y1, x2, y2 = (float(value) for value in bbox)
    total = 0
    for centre_x, centre_y, text, _score, _box in page_items:
        if x1 <= centre_x <= x2 and y1 <= centre_y <= y2:
            total += len(text.strip())
    return total


def _extract_scanned_tables(
    image: Image.Image,
    devanagari_ocr: bool,
    ocr_lines: list | None = None,
    page_number: int = 1,
) -> tuple[list[TableStructure], int]:
    """Reconstruct ruled tables from a scanned page image.

    The grid is measured from the page's own ruling (see `_ruling_regions`), so
    boundaries, rows, columns, merged cells and cell coordinates come from the
    document's geometry. The page's recognised lines then supply the text inside
    each cell together with the score that produced it, which is what makes a
    cell's confidence a real number rather than an assumed one. A table model is
    still consulted, but only for cells of a grid whose shape it agrees with, and
    only to confirm a number the page already read. On a Devanagari page an
    alphabetic cell keeps only what the page itself supports: there the English
    table model transcribes the script as Latin lookalikes, and a cell nothing
    supports is stored unreadable and flagged for review instead of guessed.
    Without a measurable ruling the model's own HTML grid is used, which keeps the
    rows but not the coordinates, and the structure says so. Per-cell crops are
    never OCR'd: each crop re-ran detection and cost tens of seconds on CPU
    (rows=6 measured at 744s/page).

    Nothing here decides whether a grid is worth showing: `app.services.table_quality`
    does, on measurements of the grid against the page it was read from. A refused
    grid comes back as a record with no cells, so it takes nothing out of the page's
    own text and shows the reader why it is not a table.

    One table failing to parse never discards the others, and the caller keeps the
    page's OCR text whichever way this goes.
    """
    engine = get_paddle_table_engine()
    tables: list[dict] = []
    img_np = np.array(image.convert("RGB"))
    if engine is not None:
        try:
            result = engine(img_np)
        except Exception as err:
            logger.warning(
                f"Table model failed on page {page_number}: {err}; preserving recognized text"
            )
            result = None
        if isinstance(result, list):
            tables = [r for r in result if isinstance(r, dict) and r.get("type") == "table"]

    # The page's own recognised lines are the text source for every script, not
    # only Devanagari ones: they carry the recogniser score that makes a cell's
    # confidence real. When the caller has none (an image processed without a
    # line pass) a Devanagari page gets one extra pass of its own.
    lines_src = ocr_lines
    if devanagari_ocr and lines_src is None:
        deva_engine = get_paddle_devanagari_engine()
        lines_src = []
        if deva_engine is not None:
            try:
                res = deva_engine.ocr(img_np, cls=False)
                lines_src = res[0] if res else []
            except Exception:
                logger.debug("Devanagari full-page line pass failed")
                lines_src = []

    page_items = _ocr_line_items(lines_src)
    # The English table model transcribes Devanagari glyph shapes as Latin
    # lookalikes ("M | B M I B" for a Marathi row). On such a page a cell is only
    # worth storing if the page's own recognised text supports it; otherwise the
    # reconstruction would inject noise that search and RAG then have to fight.
    script_page = devanagari_ocr and any(
        _DEVANAGARI_RE.search(text) for _, _, text, _, _ in page_items
    )

    structures = _structures_from_ruling(
        image, page_items, script_page, tables, page_number
    )
    if not structures:
        # No measurable ruling (a borderless layout) or no page text to place in
        # it: keep the rows the model reported, without coordinates it did not give.
        for table in tables:
            try:
                structure = _structure_from_paddle_table(
                    table, page_items, script_page, image, page_number
                )
            except Exception as table_err:
                logger.warning(
                    f"Table structure failed on page {page_number}: {table_err}; "
                    "keeping the tables that did parse"
                )
                continue
            if structure is not None and structure.cells:
                structures.append(structure)

    # Nothing above decides whether a grid is worth showing: the quality gate does,
    # on measurements of the grid itself. A refused grid is replaced by a record that
    # says why, keeps no cells, and so takes no line out of the page's own text.
    region_chars = {
        id(structure): _chars_in_region(page_items, structure.bbox)
        for structure in structures
    }
    screening = screen_structures(
        structures,
        page_size=(float(image.width), float(image.height)),
        region_chars_for=lambda structure: region_chars.get(id(structure)),
        page_lines=len(page_items),
    )
    return screening.kept, max(len(structures), len(tables))


def _extract_page_layout_text(pdf_page: Any, exclude_rects: list[Any] | None = None) -> str:
    """Extract page text preserving spatial 2D layout and table row structures.

    Blocks inside table bounding boxes are skipped when exclude_rects is given,
    so table content appears only in its structured form (no duplication).
    """
    try:
        blocks = pdf_page.get_text("blocks")
        if not blocks:
            return pdf_page.get_text()

        def in_table(b) -> bool:
            if not exclude_rects:
                return False
            cx = (b[0] + b[2]) / 2.0
            cy = (b[1] + b[3]) / 2.0
            for r in exclude_rects:
                if r[0] - 2 <= cx <= r[2] + 2 and r[1] - 2 <= cy <= r[3] + 2:
                    return True
            return False

        bands: dict[float, list[Any]] = {}
        for b in blocks:
            # b: (x0, y0, x1, y1, text, block_no, block_type)
            if len(b) > 6 and b[6] != 0:
                continue
            text = b[4].strip()
            if not text:
                continue
            if in_table(b):
                continue
            y_center = (b[1] + b[3]) / 2.0
            matched_band = None
            for y in bands:
                if abs(y - y_center) < 8.0:
                    matched_band = y
                    break
            if matched_band is None:
                matched_band = y_center
                bands[matched_band] = []
            bands[matched_band].append(b)

        sorted_bands = sorted(bands.keys())
        lines = []
        for k in sorted_bands:
            row_blocks = sorted(bands[k], key=lambda b: b[0])
            if len(row_blocks) > 1:
                row_str = " | ".join(b[4].strip().replace("\n", " ") for b in row_blocks)
            else:
                row_str = row_blocks[0][4].strip()
            lines.append(row_str)

        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Spatial layout extraction failed: {e}")
        return pdf_page.get_text()


def _extract_page_status_stamps(doc: Any, pdf_page: Any) -> list[str]:
    """Inspect small embedded raster images on a digital page to detect status stamps/banners.

    Large embedded images (page-sized scans) are skipped: they are handled by the
    full-page OCR path, so re-OCRing them here would duplicate the most expensive work.
    """
    stamps: list[str] = []
    try:
        imgs = pdf_page.get_images()
        if not imgs:
            return stamps
        rapid_engine = get_rapid_ocr_engine()
        if not rapid_engine:
            return stamps

        rect = pdf_page.rect
        for img in imgs:
            xref = img[0]
            base_img = doc.extract_image(xref)
            img_bytes = base_img.get("image")
            width = base_img.get("width", 0)
            height = base_img.get("height", 0)
            if not img_bytes or width < 25 or height < 15:
                continue
            # Skip page-sized (or larger) raster images — full-page scans
            if width >= rect.width * 1.4 and height >= rect.height * 1.4:
                continue
            # Skip images covering more than ~40% of the page area
            if width * height > rect.width * rect.height * 1.6:
                continue

            res, _ = rapid_engine(img_bytes)
            if res:
                for item in res:
                    ocr_word = item[1].strip().upper()
                    # Match discrete word tokens to prevent substring overlaps (e.g. 'PAID' in 'UNPAID')
                    tokens = set(re.findall(r"[A-Z]+", ocr_word))
                    for kw in KNOWN_STATUS_WORDS:
                        if kw in tokens:
                            normalized = "CANCELLED" if kw == "CANCELED" else kw
                            if normalized not in stamps:
                                stamps.append(normalized)
    except Exception as e:
        logger.warning(f"Image banner OCR failed: {e}")
    return stamps


def process_pdf_document(
    file_path: Path,
    language: str = "auto",
    only_pages: set[int] | None = None,
    on_page: Callable[[ExtractedPage], None] | None = None,
) -> DocumentExtractionResult:
    """
    Process PDF documents:
    - Extracts embedded text directly using PyMuPDF with 2D spatial layout preservation.
    - OCRs embedded raster image stamps/banners (e.g. PAID, UNPAID, REFUNDED, CANCELLED).
    - Identifies duplicate/near-duplicate page structures.
    - Performs automatic language detection on extracted digital text.
    - If a page has minimal or no embedded text (scanned PDF), rasterizes to image and runs PaddleOCR.
    - Aggregates all pages, detected languages, and confidence scores.

    `only_pages` restricts work to those 1-based page numbers, letting an
    interrupted document resume without re-OCRing pages already persisted.
    `on_page` is called for every finished page so the caller can commit it
    immediately; measured scanned-page cost is up to ~2 minutes, so a restart
    must not throw away everything processed so far.
    """
    try:
        import pymupdf as fitz

        t_open = time.perf_counter()
        doc = fitz.open(file_path)
        logger.info(
            "stage=pdf.open duration_s=%.3f status=ok pages=%s",
            time.perf_counter() - t_open,
            len(doc),
        )
        pages: list[ExtractedPage] = []
        ocr_applied_any = False
        engines_used: list[str] = []
        page_languages: list[str] = []
        all_confidences: list[float] = []
        page_base_hashes: dict[str, int] = {}

        for page_idx in range(len(doc)):
            page_num = page_idx + 1
            if only_pages is not None and page_num not in only_pages:
                continue
            pdf_page = doc[page_idx]
            rect = pdf_page.rect
            width = int(rect.width)
            height = int(rect.height)

            # Direct text extraction with 2D layout alignment + structured tables
            with stage("page.text_extract", page=page_num) as payload:
                page_tables, table_rects = _extract_page_tables(pdf_page)
                table_lines = _table_text_lines(page_tables, page_num)
                direct_text = _extract_page_layout_text(pdf_page, exclude_rects=table_rects)
                payload["chars"] = len(direct_text)
                payload["tables"] = len(table_rects)
            cleaned_direct = clean_extracted_text(direct_text)
            if table_lines:
                table_text = clean_extracted_text("\n".join(table_lines))
                cleaned_direct = (
                    f"{cleaned_direct}\n{table_text}".strip()
                    if cleaned_direct
                    else table_text
                )

            # Check if page has sufficient extractable text (digital PDF) or if it is scanned
            alpha_chars = sum(1 for c in cleaned_direct if c.isalnum())
            is_scanned = alpha_chars < 20

            # OCR small embedded stamps/banners only on digital pages; on scanned
            # pages the full-page OCR pass already reads stamp content.
            stamps = [] if is_scanned else _extract_page_status_stamps(doc, pdf_page)
            if stamps:
                ocr_applied_any = True
                engines_used.append("RapidOCR-Stamps")
                status_suffix = "Payment Status: " + ", ".join(stamps)
                if cleaned_direct:
                    cleaned_direct = f"{cleaned_direct}\n{status_suffix}"
                else:
                    cleaned_direct = status_suffix

            # Duplicate page detection based on normalized base text
            base_text_norm = re.sub(r"[^a-zA-Z0-9]", "", direct_text.lower())
            is_dup = False
            dup_of = None
            if len(base_text_norm) >= 30:
                if base_text_norm in page_base_hashes:
                    is_dup = True
                    dup_of = page_base_hashes[base_text_norm]
                else:
                    page_base_hashes[base_text_norm] = page_num

            # Check if page has sufficient extractable text (digital PDF) or if it is scanned
            alpha_chars = sum(1 for c in cleaned_direct if c.isalnum())
            if not is_scanned:
                # Digital text PDF page. Nothing was OCR'd, so there is no word
                # confidence to report; inventing 98% here would be presented as
                # measured accuracy.
                page_confidence = None
                det_lang, _, _ = detect_language(cleaned_direct)
                page_engine = (
                    f"PyMuPDF-Tables{len(table_rects)}" if table_rects else "PyMuPDF"
                )
                page_languages.append(det_lang)
                engines_used.append(page_engine)

                pages.append(
                    ExtractedPage(
                        page_number=page_num,
                        text=cleaned_direct,
                        confidence=page_confidence,
                        detected_language=det_lang,
                        width=width,
                        height=height,
                        status_stamps=stamps,
                        is_duplicate=is_dup,
                        duplicate_of_page=dup_of,
                        raw_text=cleaned_direct,
                        page_type="digital",
                        table_count=len(table_rects),
                        tables=page_tables,
                        engine=page_engine,
                    )
                )
                if on_page is not None:
                    on_page(pages[-1])
            else:
                # Scanned page - rasterize once; blank detection, OCR, then
                # confidence-escalated retries (preprocess, orientation).
                page_type = "scanned"
                retried = False
                ocr_failed = False
                scanned_table_lines: list[str] = []
                scanned_tables: list[Any] = []
                scanned_table_count = 0
                ocr_text, ocr_conf, ocr_engine, det_lang = "", 0.0, "None", "en"
                ocr_lines: list | None = []
                # The image the accepted text was actually read from. Escalation
                # can deskew or rotate it, and the table model has to look at the
                # same frame or its cell boxes match nothing.
                ocr_image: Image.Image | None = None
                # The turn or slant that frame differs from the page render by. A
                # quarter turn is inverted exactly; a deskew angle is not recorded
                # anywhere, so its grid says which frame it is in.
                read_rotation: int | None = None
                read_deskewed = False
                try:
                    with stage("page.render", page=page_num):
                        pix = pdf_page.get_pixmap(dpi=200)
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                    ocr_image = img

                    ink_ratio = _estimate_ink_ratio(img)
                    if ink_ratio < 0.0002:
                        page_type = "blank"
                        # Named, not "None": the viewer and the review list have
                        # to be able to tell "no ink found" from "OCR broke".
                        ocr_engine = "SkippedBlank"
                        engines_used.append("PyMuPDF+SkippedBlank")
                    else:
                        ocr_applied_any = True
                        with stage("page.ocr", page=page_num) as payload:
                            (
                                ocr_text,
                                ocr_conf,
                                ocr_engine,
                                det_lang,
                                ocr_lines,
                            ) = _perform_image_ocr(img, language=language)
                            payload["engine"] = ocr_engine
                            payload["chars"] = len(ocr_text.strip())

                        # Conservative escalation: only reprocess pages whose first pass
                        # returned nothing usable or very low confidence, plus pages whose
                        # text geometry says the scan went in sideways.
                        vertical_boxes = _boxes_imply_vertical_text(ocr_lines)
                        unusable = (
                            len(clean_extracted_text(ocr_text).strip()) < 10
                            or ocr_conf < 55.0
                        )
                        if unusable or vertical_boxes:
                            retried = True
                            if unusable:
                                with stage("page.ocr_preprocess_retry", page=page_num) as payload:
                                    prepared = preprocess_scan_image(img)
                                    if prepared is not None:
                                        (
                                            alt_text,
                                            alt_conf,
                                            alt_engine,
                                            alt_lang,
                                            alt_lines,
                                        ) = _perform_image_ocr(prepared, language=language)
                                        if len(alt_text.strip()) > len(ocr_text.strip()) and alt_conf >= ocr_conf * 0.9:
                                            ocr_text, ocr_conf, ocr_engine, det_lang = (
                                                alt_text,
                                                alt_conf,
                                                f"{alt_engine}-Preprocessed",
                                                alt_lang,
                                            )
                                            # The lines the deskewed image produced
                                            # are kept: they are in that image's own
                                            # frame, which is the frame the table
                                            # model is about to measure, so a cell
                                            # can be checked against the text the
                                            # page read instead of left to the model.
                                            ocr_lines = alt_lines
                                            ocr_image = prepared
                                            read_deskewed = True
                                    payload["chars"] = len(ocr_text.strip())

                            if vertical_boxes or len(ocr_text.strip()) < 10:
                                # Orientation correction: 90/180/270 rotations until usable text
                                with stage("page.ocr_rotation_retry", page=page_num) as payload:
                                    for rot in (90, 180, 270):
                                        rot_img = img.rotate(-rot, expand=True, fillcolor=255)
                                        (
                                            alt_text,
                                            alt_conf,
                                            alt_engine,
                                            alt_lang,
                                            alt_lines,
                                        ) = _perform_image_ocr(rot_img, language=language)
                                        if len(alt_text.strip()) >= 10 and alt_conf >= 55.0:
                                            ocr_text, ocr_conf, ocr_engine, det_lang = (
                                                alt_text,
                                                alt_conf,
                                                f"{alt_engine}-Rot{rot}",
                                                alt_lang,
                                            )
                                            # The turned image's own lines are kept
                                            # with the turned image: cells and text
                                            # stay in one frame, and a quarter turn
                                            # is mapped back onto the page later.
                                            ocr_lines = alt_lines
                                            ocr_image = rot_img
                                            read_rotation = rot
                                            payload["rotation"] = rot
                                            break
                                    payload["chars"] = len(ocr_text.strip())

                except Exception as page_error:
                    logger.warning(
                        f"Scan OCR failed on page {page_num} of {file_path}: {page_error}"
                    )
                    ocr_text, ocr_conf, ocr_engine, det_lang = "", 0.0, "Failed", "en"
                    ocr_failed = True

                # Structured table recovery for scanned pages with ruled
                # lines; independent stage so table errors NEVER discard OCR text
                from app.core.config import settings as _settings

                page_prose = ocr_text
                if (
                    not ocr_failed
                    and _settings.SCANNED_TABLE_EXTRACTION
                    and ocr_text
                    and ocr_image is not None
                    and _looks_like_ruled_table(ocr_image)
                ):
                    try:
                        with stage("page.table", page=page_num) as payload:
                            need_deva = det_lang in (
                                "hi", "mr", "en+hi", "en+mr", "uncertain"
                            )
                            page_tables, t_count = _extract_scanned_tables(
                                ocr_image,
                                devanagari_ocr=need_deva,
                                ocr_lines=ocr_lines,
                                page_number=page_num,
                            )
                            scanned_table_lines = _table_text_lines(
                                page_tables, page_num, marker="scanned-table"
                            )
                            scanned_tables = page_tables
                            scanned_table_count = t_count
                            payload["tables"] = t_count
                            payload["rows"] = len(scanned_table_lines)
                            payload["cells_uncertain"] = sum(
                                s.uncertain_cells for s in page_tables
                            )
                            payload["cells_read"] = sum(
                                s.page_backed_cells for s in page_tables
                            )
                            # Every line the grid claimed is rendered from its cell,
                            # so it must not also go through the geometric
                            # reconstruction: that put a second, differently grouped
                            # copy of each row beside the structure that had just
                            # measured it, and both were indexed. What is left over -
                            # the prose outside the ruling - is still reconstructed.
                            # A refused grid has no cells, so it takes nothing here.
                            if page_tables and ocr_lines is not None:
                                leftover = _lines_outside_structures(
                                    ocr_lines, page_tables
                                )
                                if len(leftover) != len(ocr_lines):
                                    prose, _legacy_rows, _legacy_lines = (
                                        reconstruct_reading_order_and_tables(leftover)
                                    )
                                    page_prose = prose
                                    payload["prose_lines"] = len(leftover)
                            _place_grid_on_page(
                                page_tables,
                                read_rotation,
                                read_deskewed and read_rotation is None,
                                (img.width, img.height),
                            )
                    except Exception as table_err:
                        logger.warning(
                            f"Table extraction failed on page {page_num} of {file_path}: {table_err}; preserving recognized OCR text"
                        )
                cleaned_ocr = clean_extracted_text(page_prose)
                if scanned_table_lines:
                    block = clean_extracted_text("\n".join(scanned_table_lines))
                    cleaned_ocr = (
                        f"{cleaned_ocr}\n\n{block}".strip() if cleaned_ocr else block
                    )

                if ocr_conf > 0:
                    all_confidences.append(ocr_conf)
                page_languages.append(det_lang)
                page_engine = f"PyMuPDF+{ocr_engine}" + (
                    f"-Tables{scanned_table_count}" if scanned_table_count else ""
                )
                engines_used.append(page_engine)

                pages.append(
                    ExtractedPage(
                        page_number=page_num,
                        text=cleaned_ocr or cleaned_direct,
                        confidence=ocr_conf if ocr_conf > 0 else None,
                        detected_language=det_lang,
                        width=width,
                        height=height,
                        status_stamps=stamps,
                        is_duplicate=is_dup,
                        duplicate_of_page=dup_of,
                        raw_text=ocr_text,
                        page_type=page_type,
                        ocr_retried=retried,
                        ocr_failed=ocr_failed,
                        table_count=scanned_table_count,
                        tables=scanned_tables,
                        engine=page_engine,
                    )
                )
                if on_page is not None:
                    on_page(pages[-1])

        doc.close()

        # Combined text skips duplicate pages so repeated OCR output cannot
        # dominate chunking, field extraction and embeddings.
        combined_text = "\n\n".join(
            p.text for p in pages if p.text and not p.is_duplicate
        ).strip()
        overall_conf = (
            round(sum(all_confidences) / len(all_confidences), 2)
            if all_confidences
            else 0.0
        )

        # Primary engine and document-level detected language
        if ocr_applied_any:
            engine_str = ", ".join(sorted(set(engines_used)))
        else:
            engine_str = "PyMuPDF"

        doc_detected_lang, _, _ = detect_language(combined_text)
        logger.info(
            "stage=pdf.process duration_s=%.3f status=ok pages=%s ocr_pages=%s retried=%s blank=%s tables=%s engine=%s",
            time.perf_counter() - t_open,
            len(pages),
            sum(1 for p in pages if p.page_type == "scanned"),
            sum(1 for p in pages if p.ocr_retried),
            sum(1 for p in pages if p.page_type == "blank"),
            sum(p.table_count for p in pages),
            engine_str.replace(" ", "_"),
        )

        return DocumentExtractionResult(
            pages=pages,
            combined_text=combined_text,
            overall_ocr_confidence=overall_conf,
            ocr_engine=engine_str,
            detected_language=doc_detected_lang,
            is_ocr_applied=ocr_applied_any,
            ocr_failed_pages=sum(1 for p in pages if p.ocr_failed),
        )
    except Exception as e:
        logger.error(f"Failed to process PDF {file_path}: {e}")
        return DocumentExtractionResult(
            pages=[],
            combined_text="",
            overall_ocr_confidence=0.0,
            ocr_engine="Error",
            detected_language="en",
            is_ocr_applied=False,
        )


def _office_text_result(file_path: Path, text: str, engine: str) -> DocumentExtractionResult:
    cleaned = clean_extracted_text(text)
    det_lang, _, _ = detect_language(cleaned)
    return DocumentExtractionResult(
        pages=[
            ExtractedPage(
                page_number=1,
                text=cleaned,
                # No OCR ran on this file, so no accuracy figure is claimed.
                confidence=None,
                detected_language=det_lang,
                engine=engine,
            )
        ],
        combined_text=cleaned,
        overall_ocr_confidence=0.0,
        ocr_engine=engine,
        detected_language=det_lang,
        is_ocr_applied=False,
    )


def _docx_text(path: Path) -> str:
    """Paragraphs and table rows of a .docx in document order."""
    import zipfile
    from xml.etree import ElementTree

    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def paragraph_text(element) -> str:
        return "".join(node.text or "" for node in element.iter(f"{word}t")).strip()

    lines: list[str] = []
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{word}body")
    for child in list(body or root):
        if child.tag == f"{word}p":
            text = paragraph_text(child)
            if text:
                lines.append(text)
        elif child.tag == f"{word}tbl":
            for row in child.iter(f"{word}tr"):
                cells = [
                    " ".join(
                        paragraph_text(paragraph)
                        for paragraph in cell.iter(f"{word}p")
                        if paragraph_text(paragraph)
                    ).strip()
                    for cell in row.iter(f"{word}tc")
                ]
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _xlsx_text(path: Path) -> str:
    """Rows of every worksheet of an .xlsx, prefixed by the sheet name."""
    import zipfile
    from xml.etree import ElementTree

    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    lines: list[str] = []
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in ElementTree.fromstring(
                archive.read("xl/sharedStrings.xml")
            ).iter(f"{main}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{main}t")))

        sheet_nodes = []
        if "xl/workbook.xml" in archive.namelist():
            for sheet in ElementTree.fromstring(archive.read("xl/workbook.xml")).iter(
                f"{main}sheet"
            ):
                sheet_nodes.append((sheet.get("name") or "", sheet.get(f"{rel}id") or ""))

        targets = {}
        if "xl/_rels/workbook.xml.rels" in archive.namelist():
            for rel_node in ElementTree.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            ):
                targets[rel_node.get("Id") or ""] = (rel_node.get("Target") or "").lstrip("/")

        if not sheet_nodes:
            sheet_nodes = [
                (target.rsplit("/", 1)[-1], "")
                for target in sorted(
                    name
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/") and name.endswith(".xml")
                )
            ]

        for index, (name, rel_id) in enumerate(sheet_nodes):
            target = targets.get(rel_id) or f"xl/worksheets/sheet{index + 1}.xml"
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            if target not in archive.namelist():
                continue
            lines.append(f"[sheet: {name or target}]")
            for row in ElementTree.fromstring(archive.read(target)).iter(f"{main}row"):
                cells = []
                for cell in row.iter(f"{main}c"):
                    value_node = cell.find(f"{main}v")
                    if value_node is None:
                        inline = "".join(
                            node.text or "" for node in cell.iter(f"{main}t")
                        )
                        cells.append(inline)
                        continue
                    raw = value_node.text or ""
                    if cell.get("t") == "s":
                        try:
                            cells.append(shared[int(raw)])
                        except (ValueError, IndexError):
                            cells.append(raw)
                    else:
                        cells.append(raw)
                if any(cell.strip() for cell in cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _looks_binary(content: bytes) -> bool:
    """True when a byte string is not plausibly human-readable text."""
    if not content:
        return True
    sample = content[:4096]
    controls = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 13))
    return controls > len(sample) * 0.02


def process_structured_document(
    file_path: Path, suffix: str
) -> DocumentExtractionResult:
    """Read Office documents by format instead of decoding their bytes as text.

    .docx/.xlsx are OOXML packages, so their real content is recoverable with the
    standard library. Legacy .doc/.xls are OLE compound files that none of the
    installed engines can read: they fail with an actionable message rather than
    becoming garbled text at an invented confidence.
    """
    if suffix == ".docx":
        return _office_text_result(file_path, _docx_text(file_path), "OpenXML-DOCX")
    if suffix == ".xlsx":
        return _office_text_result(file_path, _xlsx_text(file_path), "OpenXML-XLSX")
    if suffix in (".doc", ".xls"):
        raise ValueError(
            f"{suffix.upper()} files use the legacy binary format and cannot be read. "
            "Upload the document as a PDF, .docx or .xlsx instead."
        )

    raw = file_path.read_bytes()
    if _looks_binary(raw):
        raise ValueError(
            f"Unsupported or unreadable file format ({suffix or 'unknown'}). "
            "Upload a PDF, image, .docx or .xlsx."
        )
    text = raw.decode("utf-8", errors="replace")
    return _office_text_result(file_path, text, "PlainText")


def extract_document_text(
    file_path: Path,
    file_type: str,
    language: str = "auto",
    only_pages: set[int] | None = None,
    on_page: Callable[[ExtractedPage], None] | None = None,
) -> DocumentExtractionResult:
    """
    Main entry point for document text and OCR extraction with automatic language detection.
    Dispatches to PDF or Image processor based on file extension and MIME type.
    """
    suffix = file_path.suffix.lower()
    lang = (language or "auto").lower().strip()

    if suffix == ".pdf" or "pdf" in file_type.lower():
        return process_pdf_document(
            file_path, language=lang, only_pages=only_pages, on_page=on_page
        )
    elif suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp") or "image" in file_type.lower():
        return process_image_document(file_path, language=lang)
    else:
        return process_structured_document(file_path, suffix)


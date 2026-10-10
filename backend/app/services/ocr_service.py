import io
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Disable unstable oneDNN primitive execution on CPU
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_use_onednn"] = "0"

import numpy as np
from PIL import Image

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





@dataclass
class ExtractedPage:
    page_number: int
    text: str
    confidence: float
    detected_language: str = "en"
    width: int | None = None
    height: int | None = None
    page_path: str | None = None
    status_stamps: list[str] = field(default_factory=list)
    is_duplicate: bool = False
    duplicate_of_page: int | None = None


@dataclass
class DocumentExtractionResult:
    pages: list[ExtractedPage] = field(default_factory=list)
    combined_text: str = ""
    overall_ocr_confidence: float = 0.0
    ocr_engine: str = "direct_extraction"
    detected_language: str = "en"
    is_ocr_applied: bool = False


def clean_extracted_text(text: str) -> str:
    """
    Clean OCR and PDF text noise while preserving meaningful structure.
    - Preserves Devanagari script (\\u0900-\\u097F), Latin text, digits, punctuation.
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


def _perform_image_ocr(
    image: Image.Image, language: str = "auto"
) -> tuple[str, float, str, str]:
    """
    Perform OCR on a PIL Image:
    - 'auto': Runs PaddleOCR Multilingual Devanagari model, then automatically classifies language ('en', 'hi', 'mr', 'en+hi', 'en+mr', 'uncertain').
    - 'en': Runs RapidOCR baseline (with Tesseract fallback).
    - 'hi' / 'mr': Runs PaddleOCR Devanagari model with designated regional tag.

    Returns:
        tuple[extracted_text, confidence_percent, engine_name, detected_language]
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
                    texts = [item[1] for item in ocr_res]
                    scores = [float(item[2]) for item in ocr_res]
                    full_text = "\n".join(texts)
                    avg_confidence = (sum(scores) / len(scores)) if scores else 0.0
                    return full_text, round(avg_confidence * 100.0, 2), "RapidOCR", "en"
                else:
                    return "", 0.0, "RapidOCR", "en"
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
            return full_text, round(avg_conf, 2), "Tesseract", "en"
        except Exception as e:
            logger.warning(f"Tesseract English OCR fallback failed: {e}")

        return "", 0.0, "None", "en"

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
                texts = [line[1][0] for line in lines if line[1][0].strip()]
                scores = [float(line[1][1]) for line in lines if line[1][0].strip()]

                raw_text = "\n".join(texts)
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

                return raw_text, conf_pct, engine_name, detected_lang
            else:
                return "", 0.0, "PaddleOCR-Devanagari", "en"
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
                texts = [item[1] for item in ocr_res]
                scores = [float(item[2]) for item in ocr_res]
                full_text = "\n".join(texts)
                avg_confidence = (sum(scores) / len(scores)) if scores else 0.0
                det_lang, _, _ = detect_language(full_text)
                return full_text, round(avg_confidence * 100.0, 2), "RapidOCR-Fallback", det_lang
        except Exception as e:
            logger.warning(f"RapidOCR fallback failed: {e}")

    return "", 0.0, "None", "en"


def process_image_document(
    file_path: Path, language: str = "auto"
) -> DocumentExtractionResult:
    """
    Process single image documents (.png, .jpg, .jpeg) with automatic language detection.
    """
    try:
        with Image.open(file_path) as img:
            width, height = img.size
            extracted_text, confidence, engine, detected_lang = _perform_image_ocr(
                img, language=language
            )
            cleaned_text = clean_extracted_text(extracted_text)

            page = ExtractedPage(
                page_number=1,
                text=cleaned_text,
                confidence=confidence,
                detected_language=detected_lang,
                width=width,
                height=height,
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


def _extract_page_layout_text(pdf_page: Any) -> str:
    """Extract page text preserving spatial 2D layout and table row structures."""
    try:
        blocks = pdf_page.get_text("blocks")
        if not blocks:
            return pdf_page.get_text()

        bands: dict[float, list[Any]] = {}
        for b in blocks:
            # b: (x0, y0, x1, y1, text, block_no, block_type)
            if len(b) > 6 and b[6] != 0:
                continue
            text = b[4].strip()
            if not text:
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
    """Inspect embedded raster images on page to detect status stamps/banners (PAID, UNPAID, etc.)."""
    stamps: list[str] = []
    try:
        rapid_engine = get_rapid_ocr_engine()
        if not rapid_engine:
            return stamps

        imgs = pdf_page.get_images()
        for img in imgs:
            xref = img[0]
            base_img = doc.extract_image(xref)
            img_bytes = base_img.get("image")
            width = base_img.get("width", 0)
            height = base_img.get("height", 0)
            if not img_bytes or width < 25 or height < 15:
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
    file_path: Path, language: str = "auto"
) -> DocumentExtractionResult:
    """
    Process PDF documents:
    - Extracts embedded text directly using PyMuPDF with 2D spatial layout preservation.
    - OCRs embedded raster image stamps/banners (e.g. PAID, UNPAID, REFUNDED, CANCELLED).
    - Identifies duplicate/near-duplicate page structures.
    - Performs automatic language detection on extracted digital text.
    - If a page has minimal or no embedded text (scanned PDF), rasterizes to image and runs PaddleOCR.
    - Aggregates all pages, detected languages, and confidence scores.
    """
    try:
        import pymupdf as fitz

        doc = fitz.open(file_path)
        pages: list[ExtractedPage] = []
        ocr_applied_any = False
        engines_used: list[str] = []
        page_languages: list[str] = []
        all_confidences: list[float] = []
        page_base_hashes: dict[str, int] = {}

        for page_idx in range(len(doc)):
            page_num = page_idx + 1
            pdf_page = doc[page_idx]
            rect = pdf_page.rect
            width = int(rect.width)
            height = int(rect.height)

            # Direct text extraction with 2D layout alignment
            direct_text = _extract_page_layout_text(pdf_page)
            cleaned_direct = clean_extracted_text(direct_text)

            # OCR embedded stamps/banners
            stamps = _extract_page_status_stamps(doc, pdf_page)
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
            if alpha_chars >= 20:
                # Digital text PDF page
                page_confidence = 98.0
                det_lang, _, _ = detect_language(cleaned_direct)
                all_confidences.append(page_confidence)
                page_languages.append(det_lang)
                engines_used.append("PyMuPDF")

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
                    )
                )
            else:
                # Scanned page - rasterize to image and OCR with PaddleOCR
                ocr_applied_any = True
                pix = pdf_page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                ocr_text, ocr_conf, ocr_engine, det_lang = _perform_image_ocr(
                    img, language=language
                )
                cleaned_ocr = clean_extracted_text(ocr_text)

                all_confidences.append(ocr_conf)
                page_languages.append(det_lang)
                engines_used.append(f"PyMuPDF+{ocr_engine}")

                pages.append(
                    ExtractedPage(
                        page_number=page_num,
                        text=cleaned_ocr or cleaned_direct,
                        confidence=ocr_conf if ocr_conf > 0 else 50.0,
                        detected_language=det_lang,
                        width=width,
                        height=height,
                        status_stamps=stamps,
                        is_duplicate=is_dup,
                        duplicate_of_page=dup_of,
                    )
                )

        doc.close()

        combined_text = "\n\n".join(p.text for p in pages if p.text).strip()
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

        return DocumentExtractionResult(
            pages=pages,
            combined_text=combined_text,
            overall_ocr_confidence=overall_conf,
            ocr_engine=engine_str,
            detected_language=doc_detected_lang,
            is_ocr_applied=ocr_applied_any,
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


def extract_document_text(
    file_path: Path, file_type: str, language: str = "auto"
) -> DocumentExtractionResult:
    """
    Main entry point for document text and OCR extraction with automatic language detection.
    Dispatches to PDF or Image processor based on file extension and MIME type.
    """
    suffix = file_path.suffix.lower()
    lang = (language or "auto").lower().strip()

    if suffix == ".pdf" or "pdf" in file_type.lower():
        return process_pdf_document(file_path, language=lang)
    elif suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp") or "image" in file_type.lower():
        return process_image_document(file_path, language=lang)
    else:
        # Fallback for plain text or unknown
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            cleaned = clean_extracted_text(content)
            det_lang, _, _ = detect_language(cleaned)
            return DocumentExtractionResult(
                pages=[
                    ExtractedPage(
                        page_number=1,
                        text=cleaned,
                        confidence=95.0,
                        detected_language=det_lang,
                    )
                ],
                combined_text=cleaned,
                overall_ocr_confidence=95.0,
                ocr_engine="DirectText",
                detected_language=det_lang,
                is_ocr_applied=False,
            )
        except Exception as e:
            logger.error(f"Failed to extract fallback text from {file_path}: {e}")
            return DocumentExtractionResult(
                pages=[],
                combined_text="",
                overall_ocr_confidence=0.0,
                ocr_engine="None",
                detected_language="en",
                is_ocr_applied=False,
            )


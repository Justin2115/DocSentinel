import io
import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Disable unstable oneDNN primitive execution on CPU
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_use_onednn"] = "0"

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from app.core.config import settings
from app.services.ocr_quality import SEVERE_TEXT_WARNINGS, assess_ocr_text

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
    confidence: float | None
    detected_language: str = "en"
    width: int | None = None
    height: int | None = None
    page_path: str | None = None
    notes: str | None = None
    is_skipped: bool = False
    quality_warnings: list[str] = field(default_factory=list)
    retry_history: list[dict[str, Any]] = field(default_factory=list)
    confidence_provider: str | None = None
    ocr_engine: str | None = None


@dataclass
class DocumentExtractionResult:
    pages: list[ExtractedPage] = field(default_factory=list)
    combined_text: str = ""
    overall_ocr_confidence: float | None = None
    ocr_engine: str = "direct_extraction"
    detected_language: str = "en"
    is_ocr_applied: bool = False
    schema_version: str = "1.1"
    processing_status: str = "completed"
    error_message: str | None = None
    quality_warnings: list[str] = field(default_factory=list)


def get_available_tesseract_languages() -> set[str]:
    """Return the installed Tesseract language codes."""
    try:
        import pytesseract

        tessdata_path = settings.OCR_TESSDATA_PATH or None
        config = f"--tessdata-dir {tessdata_path}" if tessdata_path else None
        languages = pytesseract.get_languages(config=config) if config else pytesseract.get_languages()
        return {lang for lang in languages if isinstance(lang, str)}
    except Exception as exc:  # pragma: no cover - depends on local Tesseract install
        logger.warning("Tesseract language check failed: %s", exc)
        return set()


def detect_blank_page(image: Image.Image, threshold: float | None = None, min_text_pixels: int | None = None) -> tuple[bool, str, float]:
    """Return whether the image is effectively blank and the reason it was skipped."""
    threshold = settings.OCR_BLANK_THRESHOLD if threshold is None else threshold
    min_text_pixels = settings.OCR_MIN_TEXT_PIXELS if min_text_pixels is None else min_text_pixels

    gray = image.convert("L")
    arr = np.asarray(gray, dtype=np.uint8)
    total_pixels = arr.size
    dark_pixels = float(np.count_nonzero(arr < 245))
    dark_ratio = dark_pixels / max(total_pixels, 1)
    text_pixels = float(np.count_nonzero(arr < 200))
    avg_intensity = float(arr.mean())

    if text_pixels <= min_text_pixels and dark_ratio <= threshold:
        return True, "blank-or-nearly-blank", dark_ratio
    if avg_intensity > 245 and dark_pixels < 0.02 * total_pixels and text_pixels <= min_text_pixels:
        return True, "mostly-white-page", dark_ratio
    return False, "has-text-content", dark_ratio


def preprocess_scan_for_ocr(image: Image.Image) -> Image.Image:
    """Apply a conservative preprocessing pass for degraded scans without destroying text detail."""
    processed = image.convert("RGB")
    processed = ImageOps.grayscale(processed)
    processed = ImageOps.autocontrast(processed)
    if settings.OCR_PREPROCESS_DENOISE:
        processed = processed.filter(ImageFilter.MedianFilter(3))
    min_width, min_height = 1500, 1200
    scale = max(1.0, min_width / processed.width, min_height / processed.height)
    if scale > 1.0:
        processed = processed.resize(
            (round(processed.width * scale), round(processed.height * scale)),
            Image.Resampling.LANCZOS,
        )
    return processed


def _tesseract_language_code(language: str) -> str:
    normalized = (language or "auto").lower().strip()
    available = get_available_tesseract_languages()
    preferred = {"en": "eng", "mr": "mar", "hi": "hin", "en+mr": "eng+mar", "en+hi": "eng+hin"}
    if normalized in preferred:
        code = preferred[normalized]
        if code == "eng+mar" and "eng" in available and "mar" in available:
            return code
        if code == "eng+hin" and "eng" in available and "hin" in available:
            return code
        if code in {"eng", "mar", "hin"} and code in available:
            return code
        # An explicit language request must not silently fall back to another script.
        return ""
    if normalized == "auto":
        installed = [code for code in ("eng", "hin", "mar") if code in available]
        return "+".join(installed)
    return ""


def _run_tesseract_ocr(image: Image.Image, language: str = "auto") -> tuple[str, float, str]:
    """Run a conservative Tesseract pass for English/Marathi text on degraded or mixed scan pages."""
    try:
        import pytesseract

        tessdata_dir = settings.OCR_TESSDATA_PATH or None
        config = ["--oem", "3", "--psm", str(settings.OCR_PAGE_SEG_MODE)]
        if tessdata_dir:
            config.extend(["--tessdata-dir", tessdata_dir])

        lang_code = _tesseract_language_code(language)
        if not lang_code:
            logger.info("Tesseract skipped: requested language data is unavailable (%s)", language)
            return "", 0.0, "Tesseract-Unsupported-Language"
        data = pytesseract.image_to_data(
            image,
            config=" ".join(config),
            lang=lang_code,
            output_type=pytesseract.Output.DICT,
        )

        texts = [value for value in data.get("text", []) if value and value.strip()]
        confs = []
        for idx, word in enumerate(data.get("text", [])):
            conf = data.get("conf", [])[idx] if idx < len(data.get("conf", [])) else "-1"
            if word and word.strip():
                try:
                    score = float(conf)
                    if score >= 0:
                        confs.append(score)
                except ValueError:
                    pass

        full_text = " ".join(texts)
        avg_conf = (sum(confs) / len(confs)) if confs else 0.0
        return full_text, round(avg_conf, 2), "Tesseract"
    except Exception as exc:
        logger.warning("Tesseract OCR failed: %s", exc)
        return "", 0.0, "Tesseract"

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
    """Perform OCR on a PIL Image using the best available local engine."""
    norm_lang = (language or settings.OCR_LANGUAGE or "auto").lower().strip()

    processed_image = preprocess_scan_for_ocr(image)
    is_blank, reason, blank_ratio = detect_blank_page(processed_image)
    if is_blank:
        logger.info("Skipping blank page preflight: %s (dark_ratio=%.4f)", reason, blank_ratio)
        return "", 0.0, "Skipped-Blank", "en"

    if norm_lang == "en":
        rapid_engine = get_rapid_ocr_engine()
        if rapid_engine is not None:
            try:
                img_byte_arr = io.BytesIO()
                processed_image.convert("RGB").save(img_byte_arr, format="PNG")
                img_bytes = img_byte_arr.getvalue()

                ocr_res, _ = rapid_engine(img_bytes)
                if ocr_res:
                    texts = [item[1] for item in ocr_res]
                    scores = [float(item[2]) for item in ocr_res]
                    full_text = "\n".join(texts)
                    avg_confidence = (sum(scores) / len(scores)) if scores else 0.0
                    return full_text, round(avg_confidence * 100.0, 2), "RapidOCR", "en"
            except Exception as exc:
                logger.warning("RapidOCR extraction failed: %s", exc)

        tesseract_text, tesseract_conf, _ = _run_tesseract_ocr(processed_image, language="en")
        if tesseract_text.strip():
            return tesseract_text, tesseract_conf, "Tesseract", "en"
        return "", 0.0, "None", "en"

    paddle_engine = get_paddle_devanagari_engine()
    if paddle_engine is not None:
        try:
            img_np = np.array(processed_image.convert("RGB"))
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
                detected_lang = detect_language(raw_text)[0] if norm_lang == "auto" else norm_lang
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
        except Exception as exc:
            logger.exception("PaddleOCR extraction failed: %s", exc)

    rapid_engine = get_rapid_ocr_engine()
    if rapid_engine is not None:
        try:
            img_byte_arr = io.BytesIO()
            processed_image.convert("RGB").save(img_byte_arr, format="PNG")
            img_bytes = img_byte_arr.getvalue()
            ocr_res, _ = rapid_engine(img_bytes)
            if ocr_res:
                texts = [item[1] for item in ocr_res]
                scores = [float(item[2]) for item in ocr_res]
                full_text = "\n".join(texts)
                avg_confidence = (sum(scores) / len(scores)) if scores else 0.0
                det_lang, _, _ = detect_language(full_text)
                return full_text, round(avg_confidence * 100.0, 2), "RapidOCR-Fallback", det_lang
        except Exception as exc:
            logger.warning("RapidOCR fallback failed: %s", exc)

    tesseract_text, tesseract_conf, _ = _run_tesseract_ocr(processed_image, language=norm_lang)
    if tesseract_text.strip():
        det_lang, _, _ = detect_language(tesseract_text)
        return tesseract_text, tesseract_conf, "Tesseract", det_lang

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
            raw_warnings = assess_ocr_text(extracted_text).warnings
            cleaned_text = clean_extracted_text(extracted_text)

            page = ExtractedPage(
                page_number=1,
                text=cleaned_text,
                confidence=confidence if confidence > 0 else None,
                detected_language=detected_lang,
                width=width,
                height=height,
                confidence_provider=engine if confidence > 0 else None,
                ocr_engine=engine,
                quality_warnings=raw_warnings,
            )

            return DocumentExtractionResult(
                pages=[page],
                combined_text=cleaned_text,
                overall_ocr_confidence=confidence if confidence > 0 else None,
                ocr_engine=engine,
                detected_language=detected_lang,
                is_ocr_applied=True,
            )
    except Exception as e:
        logger.error(f"Failed to process image {file_path}: {e}")
        return DocumentExtractionResult(
            pages=[ExtractedPage(page_number=1, text="", confidence=None)],
            combined_text="",
            overall_ocr_confidence=None,
            ocr_engine="Error",
            detected_language="en",
            is_ocr_applied=True,
            processing_status="failed",
            error_message="image-ocr-provider-failed",
        )


def process_pdf_document(
    file_path: Path, language: str = "auto"
) -> DocumentExtractionResult:
    """Process one PDF using PyMuPDF first, then OCR only for scanned pages."""
    try:
        import pymupdf as fitz

        doc = fitz.open(file_path)
        pages: list[ExtractedPage] = []
        ocr_applied_any = False
        engines_used: list[str] = []
        all_confidences: list[tuple[str, float]] = []

        for page_idx in range(len(doc)):
            page_num = page_idx + 1
            pdf_page = doc[page_idx]
            rect = pdf_page.rect
            width = int(rect.width)
            height = int(rect.height)

            direct_text = pdf_page.get_text()
            direct_warnings = assess_ocr_text(
                direct_text, expected_content=bool(direct_text.strip())
            ).warnings
            cleaned_direct = clean_extracted_text(direct_text)
            alpha_chars = sum(1 for char in cleaned_direct if char.isalnum())

            if alpha_chars >= 20:
                det_lang, _, _ = detect_language(cleaned_direct)
                pages.append(
                    ExtractedPage(
                        page_number=page_num,
                        text=cleaned_direct,
                        confidence=None,
                        detected_language=det_lang,
                        width=width,
                        height=height,
                        notes="embedded-pdf-text",
                        confidence_provider=None,
                        ocr_engine="PyMuPDF",
                        quality_warnings=direct_warnings,
                    )
                )
                engines_used.append("PyMuPDF")
                continue

            pix = pdf_page.get_pixmap(dpi=settings.OCR_RENDER_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            blank, reason, blank_ratio = detect_blank_page(img)
            if blank:
                logger.info("Skipping page %s in %s: %s (dark_ratio=%.4f)", page_num, file_path.name, reason, blank_ratio)
                pages.append(
                    ExtractedPage(
                        page_number=page_num,
                        text="",
                        confidence=None,
                        detected_language="en",
                        width=width,
                        height=height,
                        notes=f"skipped:{reason}",
                        is_skipped=True,
                        ocr_engine="blank-page-detector",
                    )
                )
                continue

            ocr_applied_any = True
            ocr_text, ocr_conf, ocr_engine, det_lang = _perform_image_ocr(img, language=language)
            raw_ocr_warnings = assess_ocr_text(ocr_text).warnings
            cleaned_ocr = clean_extracted_text(ocr_text)
            severe_ocr_warnings = set(raw_ocr_warnings) & SEVERE_TEXT_WARNINGS
            if severe_ocr_warnings:
                logger.warning(
                    "Rejected PDF OCR output page=%s categories=%s",
                    page_num,
                    sorted(severe_ocr_warnings),
                )
                cleaned_ocr = ""
                raw_ocr_warnings = [
                    warning for warning in raw_ocr_warnings
                    if warning not in SEVERE_TEXT_WARNINGS
                ] + ["ocr-output-rejected"]
            if ocr_conf > 0:
                all_confidences.append((ocr_engine, ocr_conf))
            engines_used.append(f"PyMuPDF+{ocr_engine}")

            pages.append(
                ExtractedPage(
                    page_number=page_num,
                    text=cleaned_ocr or cleaned_direct,
                    confidence=ocr_conf if ocr_conf > 0 else None,
                    detected_language=det_lang,
                    width=width,
                    height=height,
                    notes="ocr-scanned-page",
                    quality_warnings=direct_warnings + raw_ocr_warnings,
                    confidence_provider=ocr_engine if ocr_conf > 0 else None,
                    ocr_engine=ocr_engine,
                )
            )

        doc.close()

        combined_text = "\n\n".join(page.text for page in pages if page.text).strip()
        confidence_providers = {provider for provider, _ in all_confidences}
        overall_conf = (
            round(sum(score for _, score in all_confidences) / len(all_confidences), 2)
            if all_confidences and len(confidence_providers) == 1
            else None
        )
        engine_str = ", ".join(sorted(set(engines_used))) if ocr_applied_any else "PyMuPDF"
        doc_detected_lang, _, _ = detect_language(combined_text)

        return DocumentExtractionResult(
            pages=pages,
            combined_text=combined_text,
            overall_ocr_confidence=overall_conf,
            ocr_engine=engine_str,
            detected_language=doc_detected_lang,
            is_ocr_applied=ocr_applied_any,
        )
    except Exception as exc:
        logger.error("Failed to process PDF %s: %s", file_path, exc)
        return DocumentExtractionResult(
            pages=[],
            combined_text="",
            overall_ocr_confidence=None,
            ocr_engine="Error",
            detected_language="en",
            is_ocr_applied=False,
            processing_status="failed",
            error_message="pdf-parser-or-ocr-failed",
        )
def extract_document_text(
    file_path: Path, file_type: str, language: str = "auto"
) -> DocumentExtractionResult:
    """Route verified file content to its native parser or the existing OCR path."""
    started = time.perf_counter()
    suffix = file_path.suffix.lower()
    language = (language or "auto").lower().strip()
    try:
        kind = _detect_document_kind(file_path, file_type)
        logger.info("Detected document kind=%s suffix=%s mime=%s", kind, suffix, file_type)
        if kind == "pdf":
            result = process_pdf_document(file_path, language=language)
        elif kind == "image":
            result = process_image_document(file_path, language=language)
        elif kind == "docx":
            result = _extract_docx(file_path, language=language)
        elif kind == "xlsx":
            result = _extract_xlsx(file_path, language=language)
        elif kind == "text":
            result = _extract_plain_text(file_path)
        else:  # pragma: no cover - guarded by _detect_document_kind
            raise ValueError("unsupported-file-format")
        _validate_extraction_result(result)
        logger.info(
            "Document extraction finished kind=%s method=%s pages=%d status=%s warnings=%s elapsed=%.3fs",
            kind,
            result.ocr_engine,
            len(result.pages),
            result.processing_status,
            result.quality_warnings,
            time.perf_counter() - started,
        )
        return result
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        reason = str(exc) or "invalid-or-unsupported-document"
        logger.warning(
            "Document extraction rejected suffix=%s category=%s elapsed=%.3fs",
            suffix,
            reason,
            time.perf_counter() - started,
        )
        return DocumentExtractionResult(
            pages=[], combined_text="", ocr_engine="Error",
            detected_language="en", is_ocr_applied=False,
            processing_status="failed", error_message=reason,
        )
    except Exception:
        logger.exception("Document extraction failed suffix=%s", suffix)
        return DocumentExtractionResult(
            pages=[], combined_text="", ocr_engine="Error",
            detected_language="en", is_ocr_applied=False,
            processing_status="failed", error_message="document-extraction-failed",
        )


def _detect_document_kind(file_path: Path, claimed_mime: str | None = None) -> str:
    if not file_path.is_file():
        raise ValueError("uploaded-file-not-found")
    with file_path.open("rb") as source:
        header = source.read(8192)
    suffix = file_path.suffix.lower()
    if b"%PDF-" in header[:1024]:
        kind = "pdf"
    elif zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path) as package:
            names = set(package.namelist())
            if "[Content_Types].xml" not in names:
                raise ValueError("invalid-office-package")
            if "word/document.xml" in names:
                kind = "docx"
            elif "xl/workbook.xml" in names:
                kind = "xlsx"
            else:
                raise ValueError("unsupported-zip-document")
    else:
        try:
            with Image.open(file_path) as image:
                image.verify()
            kind = "image"
        except Exception:
            if suffix in {".txt", ".csv", ".md"}:
                if b"\x00" in header:
                    raise ValueError("binary-content-in-text-file")
                kind = "text"
            elif suffix in {".doc", ".xls"}:
                raise ValueError("legacy-office-format-not-supported; save as DOCX/XLSX or PDF")
            else:
                raise ValueError("unsupported-or-unrecognized-file-format")

    expected_mime = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text": "text/",
        "image": "image/",
    }[kind]
    if claimed_mime and claimed_mime != "application/octet-stream":
        matches = claimed_mime.startswith(expected_mime) if expected_mime.endswith("/") else claimed_mime == expected_mime
        if not matches:
            logger.warning("Upload MIME mismatch: signature detected %s, client declared %s", kind, claimed_mime)
    return kind


def _extract_plain_text(file_path: Path) -> DocumentExtractionResult:
    try:
        content = file_path.read_bytes().decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("text-file-is-not-valid-UTF-8") from exc
    raw_warnings = assess_ocr_text(content).warnings
    cleaned = clean_extracted_text(content)
    det_lang, _, _ = detect_language(cleaned)
    return DocumentExtractionResult(
        pages=[ExtractedPage(
            page_number=1,
            text=cleaned,
            confidence=None,
            detected_language=det_lang,
            quality_warnings=raw_warnings,
        )],
        combined_text=cleaned,
        ocr_engine="UTF-8 text extraction",
        detected_language=det_lang,
        is_ocr_applied=False,
    )


def _extract_docx(file_path: Path, language: str) -> DocumentExtractionResult:
    from docx import Document as WordDocument
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = WordDocument(str(file_path))
    except Exception as exc:
        raise ValueError("malformed-docx-package") from exc
    parts: list[str] = []
    table_number = 0
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            value = clean_extracted_text(paragraph.text)
            if value:
                style = (paragraph.style.name or "") if paragraph.style else ""
                parts.append(f"{style}: {value}" if style.lower().startswith("heading") else value)
        elif child.tag == qn("w:tbl"):
            table_number += 1
            table = Table(child, document)
            rows = [[clean_extracted_text(cell.text) for cell in row.cells] for row in table.rows]
            rows = [row for row in rows if any(row)]
            if not rows:
                continue
            parts.append(f"Table {table_number}:")
            headers = rows[0]
            parts.append("Headers: " + " | ".join(headers))
            for row_number, row in enumerate(rows[1:], start=2):
                cells = [
                    f"{headers[index] or f'Column {index + 1}'}: {value}"
                    for index, value in enumerate(row) if value
                ]
                if cells:
                    parts.append(f"Row {row_number}: " + " | ".join(cells))

    image_texts: list[str] = []
    image_engines: set[str] = set()
    image_warnings: list[str] = []
    seen_images: set[str] = set()
    for relationship in document.part.rels.values():
        if not relationship.reltype.endswith("/image"):
            continue
        part = relationship.target_part
        part_name = str(part.partname)
        if part_name in seen_images:
            continue
        seen_images.add(part_name)
        try:
            image = Image.open(io.BytesIO(part.blob)).convert("RGB")
            text, _, engine, _ = _perform_image_ocr(image, language=language)
            assessment = assess_ocr_text(text)
            if set(assessment.warnings) & SEVERE_TEXT_WARNINGS:
                image_warnings.append("embedded-image-ocr-rejected")
                continue
            text = clean_extracted_text(text)
            if text:
                image_texts.append(text)
                image_engines.add(engine)
                logger.info("Embedded DOCX image OCR provider=%s", engine)
        except Exception:
            logger.exception("Embedded DOCX image OCR failed")
    if image_texts:
        parts.extend(["Embedded image text:", *image_texts])
    full_text = "\n\n".join(part for part in parts if part).strip()
    detected, _, _ = detect_language(full_text)
    return DocumentExtractionResult(
        pages=[ExtractedPage(page_number=1, text=full_text, confidence=None, detected_language=detected,
                             notes=f"DOCX native extraction; {table_number} tables",
                             quality_warnings=image_warnings)],
        combined_text=full_text,
        ocr_engine="python-docx" + (f"+{','.join(sorted(image_engines))}" if image_engines else ""),
        detected_language=detected,
        is_ocr_applied=bool(image_texts),
    )


def _extract_xlsx(file_path: Path, language: str) -> DocumentExtractionResult:
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(file_path, read_only=False, data_only=False)
    except Exception as exc:
        raise ValueError("malformed-xlsx-package") from exc
    pages: list[ExtractedPage] = []
    all_text: list[str] = []
    image_engines: set[str] = set()
    image_warnings: list[str] = []
    try:
        for sheet_number, worksheet in enumerate(workbook.worksheets, start=1):
            lines = [f"Worksheet: {worksheet.title}"]
            for row in worksheet.iter_rows():
                values = [(cell.coordinate, str(cell.value).strip()) for cell in row if cell.value is not None]
                if values:
                    lines.append(" | ".join(f"{coordinate}: {value}" for coordinate, value in values))
            for image in getattr(worksheet, "_images", []):
                try:
                    anchor = image.anchor._from
                    location = f"{worksheet.cell(anchor.row + 1, anchor.col + 1).coordinate}"
                    recognized, _, engine, _ = _perform_image_ocr(
                        Image.open(io.BytesIO(image._data())).convert("RGB"), language=language
                    )
                    assessment = assess_ocr_text(recognized)
                    if set(assessment.warnings) & SEVERE_TEXT_WARNINGS:
                        image_warnings.append("embedded-image-ocr-rejected")
                        continue
                    recognized = clean_extracted_text(recognized)
                    if recognized:
                        lines.append(f"Embedded image at {location}: {recognized}")
                        image_engines.add(engine)
                        logger.info("Embedded XLSX image OCR provider=%s", engine)
                except Exception:
                    logger.exception("Embedded XLSX image OCR failed sheet=%s", worksheet.title)
            text = "\n".join(lines).strip()
            detected, _, _ = detect_language(text)
            pages.append(ExtractedPage(
                page_number=sheet_number,
                text=text,
                confidence=None,
                detected_language=detected,
                notes=f"worksheet:{worksheet.title}",
                quality_warnings=list(image_warnings),
            ))
            all_text.append(text)
    finally:
        workbook.close()
    combined = "\n\n".join(all_text).strip()
    detected, _, _ = detect_language(combined)
    return DocumentExtractionResult(
        pages=pages,
        combined_text=combined,
        ocr_engine="openpyxl" + (f"+{','.join(sorted(image_engines))}" if image_engines else ""),
        detected_language=detected,
        is_ocr_applied=any("Embedded image at" in text for text in all_text),
    )


def _validate_extraction_result(result: DocumentExtractionResult) -> None:
    warnings: list[str] = []
    severe_detected = False
    valid_text: list[str] = []
    for page in result.pages:
        if page.is_skipped:
            continue
        assessment = assess_ocr_text(page.text)
        page_warnings = list(dict.fromkeys(page.quality_warnings + assessment.warnings))
        page.quality_warnings = page_warnings
        warnings.extend(page_warnings)
        severe = set(page_warnings) & SEVERE_TEXT_WARNINGS
        if severe:
            logger.warning("Rejected suspicious extraction page=%s categories=%s", page.page_number, sorted(severe))
            page.text = ""
            severe_detected = True
        elif page.text.strip():
            valid_text.append(page.text.strip())
    result.combined_text = "\n\n".join(valid_text).strip()
    result.quality_warnings = list(dict.fromkeys(warnings))
    if severe_detected:
        result.processing_status = "needs_review"
    elif warnings:
        result.processing_status = "completed_with_warnings"
    else:
        result.processing_status = "completed"
    if not result.combined_text:
        result.processing_status = "failed"
        result.error_message = result.error_message or "no-usable-text-extracted"


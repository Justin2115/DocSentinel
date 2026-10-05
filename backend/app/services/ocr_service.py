import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any

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

# Initialize Surya lazily (v1 FoundationPredictor, or v2 InferenceManager)
_surya_runtime: dict[str, Any] | None | bool = None
_surya_worker: subprocess.Popen[str] | None = None
_surya_worker_lock = threading.Lock()
_MAX_OCR_SIDE = 1600
_WORKER_PAGE_TIMEOUT = 300


def _html_to_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


def get_surya_runtime() -> dict[str, Any] | None:
    """Load Surya once. Prefer v1 (pure Torch) so Windows does not need llama.cpp."""
    global _surya_runtime
    if _surya_runtime is False:
        return None
    if _surya_runtime is not None:
        return _surya_runtime

    try:
        try:
            from surya.detection import DetectionPredictor
            from surya.foundation import FoundationPredictor
            from surya.recognition import RecognitionPredictor
            from surya.settings import settings

            foundation = FoundationPredictor(
                checkpoint=settings.RECOGNITION_MODEL_CHECKPOINT
            )
            det = DetectionPredictor()
            rec = RecognitionPredictor(foundation)
            _surya_runtime = {"kind": "v1", "rec": rec, "det": det}
            logger.info("Surya OCR ready (v1 FoundationPredictor)")
            return _surya_runtime
        except ImportError:
            from surya.inference import SuryaInferenceManager
            from surya.recognition import RecognitionPredictor

            manager = SuryaInferenceManager()
            rec = RecognitionPredictor(manager)
            _surya_runtime = {"kind": "v2", "rec": rec, "det": None}
            logger.info("Surya OCR ready (v2 SuryaInferenceManager)")
            return _surya_runtime
    except Exception:
        logger.exception("Could not initialize Surya OCR")
        _surya_runtime = False
        return None


def _prediction_to_text(pred: Any) -> tuple[str, float]:
    lines: list[str] = []
    scores: list[float] = []

    text_lines = getattr(pred, "text_lines", None)
    if text_lines:
        for line in text_lines:
            token = (getattr(line, "text", None) or "").strip()
            if not token:
                continue
            lines.append(token)
            conf = getattr(line, "confidence", None)
            if conf is not None:
                scores.append(float(conf))
    else:
        for block in getattr(pred, "blocks", None) or []:
            html = getattr(block, "html", None) or getattr(block, "text", "") or ""
            token = _html_to_text(str(html))
            if not token:
                continue
            lines.append(token)
            conf = getattr(block, "confidence", None)
            if conf is not None:
                scores.append(float(conf))

    text = "\n".join(lines)
    if not text:
        return "", 0.0
    if scores:
        avg = sum(scores) / len(scores)
        if avg <= 1.0:
            avg *= 100.0
        return text, round(avg, 2)
    return text, 85.0


def _engine_label(detected_lang: str) -> str:
    labels = {
        "mr": "SuryaOCR-Marathi",
        "hi": "SuryaOCR-Hindi",
        "en+mr": "SuryaOCR-Mixed-Marathi",
        "en+hi": "SuryaOCR-Mixed-Hindi",
        "en": "SuryaOCR-English",
    }
    return labels.get(detected_lang, "SuryaOCR")

@dataclass
class ExtractedPage:
    page_number: int
    text: str
    confidence: float
    detected_language: str = "en"
    width: int | None = None
    height: int | None = None
    page_path: str | None = None


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


def _prepare_ocr_image(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    width, height = rgb.size
    longest = max(width, height)
    if longest <= _MAX_OCR_SIDE:
        return rgb
    scale = _MAX_OCR_SIDE / float(longest)
    return rgb.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.Resampling.LANCZOS,
    )


def _run_surya_on_image(
    image: Image.Image, language: str = "auto"
) -> tuple[str, float, str, str]:
    """Run Surya in this process. Used by the OCR child, not the API process."""
    norm_lang = (language or "auto").lower().strip()
    runtime = get_surya_runtime()
    if runtime is None:
        return "", 0.0, "None", "en"

    try:
        rgb = _prepare_ocr_image(image)
        rec = runtime["rec"]
        if runtime["kind"] == "v1":
            predictions = rec([rgb], det_predictor=runtime["det"])
        else:
            predictions = rec([rgb])

        if not predictions:
            return "", 0.0, "SuryaOCR", "en"

        raw_text, conf_pct = _prediction_to_text(predictions[0])
        if norm_lang == "auto":
            detected_lang, _, _ = detect_language(raw_text)
        else:
            detected_lang = norm_lang
        return raw_text, conf_pct, _engine_label(detected_lang), detected_lang
    except Exception:
        logger.exception("Surya OCR extraction failed")
        return "", 0.0, "None", "en"


def _worker_env() -> dict[str, str]:
    env = os.environ.copy()
    env["DOC_SENTINEL_SURYA_CHILD"] = "1"
    env["TQDM_DISABLE"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("TORCH_NUM_THREADS", "1")
    return env


def _stop_surya_worker() -> None:
    global _surya_worker
    proc = _surya_worker
    _surya_worker = None
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _ensure_surya_worker() -> subprocess.Popen[str]:
    global _surya_worker
    if _surya_worker is not None and _surya_worker.poll() is None:
        return _surya_worker

    _stop_surya_worker()
    backend_dir = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.services.surya_worker", "--serve"],
        cwd=str(backend_dir),
        env=_worker_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def _log_stderr() -> None:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            text = line.rstrip()
            if text:
                logger.info("surya worker: %s", text)

    threading.Thread(target=_log_stderr, name="surya-worker-stderr", daemon=True).start()

    try:
        payload = _read_json_from_worker(proc, timeout=300)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    if proc.poll() is not None or not payload.get("ready"):
        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError(f"Surya worker failed to start: {payload}")

    _surya_worker = proc
    logger.info("Surya OCR worker ready pid=%s", proc.pid)
    return proc


def _read_worker_line(proc: subprocess.Popen[str], timeout: int) -> str:
    line_holder: list[str] = []

    def _read() -> None:
        if proc.stdout is None:
            return
        line_holder.append(proc.stdout.readline())

    reader = threading.Thread(target=_read, name="surya-worker-stdout", daemon=True)
    reader.start()
    reader.join(timeout=timeout)
    if reader.is_alive():
        raise TimeoutError(f"Surya worker timed out after {timeout}s")
    return line_holder[0] if line_holder else ""


def _read_json_from_worker(proc: subprocess.Popen[str], timeout: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(1, int(deadline - time.time()))
        raw = _read_worker_line(proc, remaining)
        if not (raw or "").strip():
            if proc.poll() is not None:
                raise RuntimeError(f"Surya worker exited {proc.returncode}")
            continue
        try:
            parsed = json.loads(raw.strip())
        except json.JSONDecodeError:
            logger.info("surya worker stdout: %s", raw.strip()[:500])
            continue
        if isinstance(parsed, dict):
            return parsed
    raise TimeoutError(f"Surya worker timed out after {timeout}s")


def _run_surya_subprocess(
    image: Image.Image, language: str = "auto"
) -> tuple[str, float, str, str]:
    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    image_path = handle.name
    handle.close()
    try:
        _prepare_ocr_image(image).save(image_path, "PNG")
        request = json.dumps({"image": image_path, "language": language or "auto"})
        with _surya_worker_lock:
            last_error = ""
            for attempt in range(2):
                try:
                    proc = _ensure_surya_worker()
                    if proc.stdin is None or proc.stdout is None:
                        raise RuntimeError("Surya worker pipes are closed")
                    proc.stdin.write(request + "\n")
                    proc.stdin.flush()
                    payload = _read_json_from_worker(proc, _WORKER_PAGE_TIMEOUT)
                    if payload.get("error") and not payload.get("engine"):
                        last_error = str(payload.get("error"))
                        continue
                    return (
                        payload.get("text") or "",
                        float(payload.get("confidence") or 0),
                        payload.get("engine") or "None",
                        payload.get("language") or "en",
                    )
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning("Surya OCR worker attempt %s failed: %s", attempt + 1, exc)
                    _stop_surya_worker()
            logger.error("Surya OCR worker failed after retries: %s", last_error)
            return "", 0.0, "None", "en"
    finally:
        Path(image_path).unlink(missing_ok=True)


def _perform_image_ocr(
    image: Image.Image, language: str = "auto"
) -> tuple[str, float, str, str]:
    """
    Run Surya OCR on a PIL image, then classify language from the extracted text.

    The API process uses a child Python process so a native Surya/Torch crash
    does not take down FastAPI.
    """
    if os.environ.get("DOC_SENTINEL_SURYA_CHILD") == "1":
        return _run_surya_on_image(image, language)
    return _run_surya_subprocess(image, language)


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


def process_pdf_document(
    file_path: Path, language: str = "auto"
) -> DocumentExtractionResult:
    """
    Process PDF documents:
    - Extracts embedded text directly using PyMuPDF.
    - Performs automatic language detection on extracted digital text.
    - If a page has minimal or no embedded text (scanned PDF), rasterizes to image and runs Surya OCR.
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

        for page_idx in range(len(doc)):
            page_num = page_idx + 1
            pdf_page = doc[page_idx]
            rect = pdf_page.rect
            width = int(rect.width)
            height = int(rect.height)

            # Direct text extraction
            direct_text = pdf_page.get_text()
            cleaned_direct = clean_extracted_text(direct_text)

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
                    )
                )
            else:
                # Scanned page - rasterize to image and OCR with Surya
                ocr_applied_any = True
                pix = pdf_page.get_pixmap(dpi=120)
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


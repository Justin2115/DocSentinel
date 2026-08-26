import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# Initialize RapidOCR lazily
_rapid_ocr_engine = None


def get_rapid_ocr_engine():
    global _rapid_ocr_engine
    if _rapid_ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _rapid_ocr_engine = RapidOCR()
        except Exception as e:
            logger.warning(f"Could not initialize RapidOCR engine: {e}")
            _rapid_ocr_engine = False
    return _rapid_ocr_engine if _rapid_ocr_engine is not False else None


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    confidence: float
    width: int | None = None
    height: int | None = None
    page_path: str | None = None


@dataclass
class DocumentExtractionResult:
    pages: list[ExtractedPage] = field(default_factory=list)
    combined_text: str = ""
    overall_ocr_confidence: float = 0.0
    ocr_engine: str = "direct_extraction"
    is_ocr_applied: bool = False


def clean_extracted_text(text: str) -> str:
    """
    Clean OCR and PDF text noise while preserving meaningful structure.
    - Removes non-printable control characters (except newline, tab).
    - Replaces weird whitespace and repeated symbols.
    - Normalizes consecutive blank lines.
    """
    if not text:
        return ""

    # Remove non-printable control characters
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


def _perform_image_ocr(image: Image.Image) -> tuple[str, float, str]:
    """
    Perform OCR on a PIL Image with automatic fallback:
    1. RapidOCR (self-contained ONNX engine)
    2. pytesseract (if installed on system)
    3. Graceful fallback returning empty string if no OCR engine succeeds.
    """
    # 1. Try RapidOCR
    rapid_engine = get_rapid_ocr_engine()
    if rapid_engine is not None:
        try:
            # Convert PIL image to bytes or numpy array
            img_byte_arr = io.BytesIO()
            image.convert("RGB").save(img_byte_arr, format="PNG")
            img_bytes = img_byte_arr.getvalue()

            ocr_res, _ = rapid_engine(img_bytes)
            if ocr_res:
                texts = []
                scores = []
                for item in ocr_res:
                    # item structure: [box_points, text, confidence_score]
                    text_content = item[1]
                    score = float(item[2])
                    texts.append(text_content)
                    scores.append(score)

                full_text = "\n".join(texts)
                avg_confidence = (sum(scores) / len(scores)) if scores else 0.0
                return full_text, round(avg_confidence * 100.0, 2), "RapidOCR"
            else:
                return "", 0.0, "RapidOCR"
        except Exception as e:
            logger.warning(f"RapidOCR extraction failed: {e}")


    # 2. Try pytesseract
    try:
        import pytesseract

        data = pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT
        )
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
        return full_text, round(avg_conf, 2), "Tesseract"
    except Exception as e:
        logger.warning(f"Tesseract OCR failed/not available: {e}")

    return "", 0.0, "None"


def process_image_document(file_path: Path) -> DocumentExtractionResult:
    """
    Process single image documents (.png, .jpg, .jpeg).
    """
    try:
        with Image.open(file_path) as img:
            width, height = img.size
            extracted_text, confidence, engine = _perform_image_ocr(img)
            cleaned_text = clean_extracted_text(extracted_text)

            page = ExtractedPage(
                page_number=1,
                text=cleaned_text,
                confidence=confidence,
                width=width,
                height=height,
            )

            return DocumentExtractionResult(
                pages=[page],
                combined_text=cleaned_text,
                overall_ocr_confidence=confidence,
                ocr_engine=engine,
                is_ocr_applied=True,
            )
    except Exception as e:
        logger.error(f"Failed to process image {file_path}: {e}")
        return DocumentExtractionResult(
            pages=[ExtractedPage(page_number=1, text="", confidence=0.0)],
            combined_text="",
            overall_ocr_confidence=0.0,
            ocr_engine="Error",
            is_ocr_applied=True,
        )


def process_pdf_document(file_path: Path) -> DocumentExtractionResult:
    """
    Process PDF documents:
    - Extracts embedded text directly using PyMuPDF.
    - If a page has minimal or no embedded text (scanned PDF), rasterizes to image and runs OCR.
    - Aggregates all pages and confidence scores.
    """
    try:
        import pymupdf as fitz

        doc = fitz.open(file_path)
        pages: list[ExtractedPage] = []
        ocr_applied_any = False
        engine_used = "PyMuPDF"
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

            # Check if page has sufficient extractable text or if it is scanned
            # If fewer than 20 alphanumeric characters, treat as scanned page
            alpha_chars = sum(1 for c in cleaned_direct if c.isalnum())
            if alpha_chars >= 20:
                # Digital text PDF page
                page_confidence = 98.0
                all_confidences.append(page_confidence)
                pages.append(
                    ExtractedPage(
                        page_number=page_num,
                        text=cleaned_direct,
                        confidence=page_confidence,
                        width=width,
                        height=height,
                    )
                )
            else:
                # Scanned page - rasterize to image and OCR
                ocr_applied_any = True
                pix = pdf_page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                ocr_text, ocr_conf, ocr_engine = _perform_image_ocr(img)
                engine_used = f"PyMuPDF+{ocr_engine}"
                cleaned_ocr = clean_extracted_text(ocr_text)

                all_confidences.append(ocr_conf)
                pages.append(
                    ExtractedPage(
                        page_number=page_num,
                        text=cleaned_ocr or cleaned_direct,
                        confidence=ocr_conf if ocr_conf > 0 else 50.0,
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

        return DocumentExtractionResult(
            pages=pages,
            combined_text=combined_text,
            overall_ocr_confidence=overall_conf,
            ocr_engine=engine_used,
            is_ocr_applied=ocr_applied_any,
        )
    except Exception as e:
        logger.error(f"Failed to process PDF {file_path}: {e}")
        return DocumentExtractionResult(
            pages=[],
            combined_text="",
            overall_ocr_confidence=0.0,
            ocr_engine="Error",
            is_ocr_applied=False,
        )


def extract_document_text(file_path: Path, file_type: str) -> DocumentExtractionResult:
    """
    Main entry point for document text and OCR extraction.
    Dispatches to PDF or Image processor based on file extension and MIME type.
    """
    suffix = file_path.suffix.lower()

    if suffix == ".pdf" or "pdf" in file_type.lower():
        return process_pdf_document(file_path)
    elif suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp") or "image" in file_type.lower():
        return process_image_document(file_path)
    else:
        # Fallback for plain text or unknown
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            cleaned = clean_extracted_text(content)
            return DocumentExtractionResult(
                pages=[
                    ExtractedPage(
                        page_number=1,
                        text=cleaned,
                        confidence=95.0,
                    )
                ],
                combined_text=cleaned,
                overall_ocr_confidence=95.0,
                ocr_engine="DirectText",
                is_ocr_applied=False,
            )
        except Exception as e:
            logger.error(f"Failed to extract fallback text from {file_path}: {e}")
            return DocumentExtractionResult(
                pages=[],
                combined_text="",
                overall_ocr_confidence=0.0,
                ocr_engine="None",
                is_ocr_applied=False,
            )

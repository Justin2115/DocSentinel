from abc import ABC, abstractmethod
from pathlib import Path

import pytesseract

from backend.app.core.config import TESSERACT_CMD
from backend.app.schemas.response_schemas import PreprocessedPage


class OCREngine(ABC):
    """Interface for OCR engine implementations."""

    @abstractmethod
    def process(self, page: PreprocessedPage) -> str:
        """Extract text from a preprocessed page."""
        raise NotImplementedError


class TesseractEngine(OCREngine):
    """Tesseract-based OCR engine implementation."""

    engine_name = "tesseract"

    def __init__(self, tesseract_cmd: str = TESSERACT_CMD):
        self.tesseract_cmd = tesseract_cmd
        pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd

    def process(self, page: PreprocessedPage) -> str:
        """Run Tesseract OCR on a preprocessed page."""

        image_path = Path(page.image_path)

        if not image_path.exists():
            raise FileNotFoundError(
                f"Preprocessed image not found: {page.image_path}"
            )

        if not image_path.is_file():
            raise ValueError(
                f"Preprocessed image path is not a file: {page.image_path}"
            )

        try:
            return pytesseract.image_to_string(str(image_path))
        except pytesseract.TesseractError as exc:
            raise RuntimeError(
                f"Tesseract OCR failed for page {page.page_number}"
            ) from exc
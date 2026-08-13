import os


TESSERACT_CMD = os.getenv(
    "TESSERACT_CMD",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)

OCR_ENGINE = os.getenv("OCR_ENGINE", "tesseract")
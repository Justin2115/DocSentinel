import io
import sys
from pathlib import Path

# Configure utf-8 standard output for Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import fitz  # PyMuPDF
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from app.db.session import SessionLocal
from app.main import app
from app.models.document import Document, DocumentPage, OCRResult

BACKEND_DIR = Path(__file__).resolve().parents[1]
UPLOADS_DIR = BACKEND_DIR / "uploads"

client = TestClient(app)

# Helper to find Indic Devanagari font
def get_devanagari_font(size: int = 36):
    font_paths = [
        "C:/Windows/Fonts/Nirmala.ttc",
        "C:/Windows/Fonts/nirmala.ttf",
        "C:/Windows/Fonts/mangal.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for p in font_paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def create_indic_image(text: str, width: int = 700, height: int = 160) -> io.BytesIO:
    font = get_devanagari_font(36)
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((30, 50), text, fill=(0, 0, 0), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def create_scanned_indic_pdf(text: str) -> io.BytesIO:
    font = get_devanagari_font(36)
    doc = fitz.open()
    # Create a 2-page scanned PDF with rasterized images
    for page_num in range(2):
        img = Image.new("RGB", (800, 200), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        page_text = f"{text} (Page {page_num + 1})"
        draw.text((40, 60), page_text, fill=(0, 0, 0), font=font)

        img_buf = io.BytesIO()
        img.save(img_buf, format="PNG")
        img_bytes = img_buf.getvalue()

        # Insert page with image
        pdf_page = doc.new_page(width=600, height=200)
        pdf_page.insert_image(pdf_page.rect, stream=img_bytes)

    pdf_buf = io.BytesIO()
    doc.save(pdf_buf)
    doc.close()
    pdf_buf.seek(0)
    return pdf_buf


print("==================================================")
print("TEST 1: English Multi-page PDF (Regression Test)")
print("==================================================")
pdf_file_path = UPLOADS_DIR / "a8893ca61e5e427baf943f4d7dfcac44.pdf"
pdf_bytes = pdf_file_path.read_bytes()
resp1 = client.post(
    "/api/documents/upload",
    files={"file": ("Application_Form_Test.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    data={"language": "en"},
)
assert resp1.status_code == 201, f"Expected 201, got {resp1.status_code}: {resp1.text}"
doc1 = resp1.json()
print(f"Uploaded English PDF ID: {doc1['id']}, Status: {doc1['status']}, Confidence: {doc1['overall_confidence']}")
assert doc1["status"] == "completed"
assert doc1["document_type"] == "Application Form"

detail1 = client.get(f"/api/documents/{doc1['id']}").json()
print(f"Pages: {len(detail1.get('pages', []))}, Extracted Fields: {len(detail1.get('extracted_fields', []))}")
assert len(detail1.get("pages", [])) == 5
assert len(detail1.get("extracted_fields", [])) >= 5


print("\n==================================================")
print("TEST 2: English PNG Image (Regression Test)")
print("==================================================")
img_file_path = UPLOADS_DIR / "bd36b5e9c49148759966f2367e5ca7bc.png"
img_bytes = img_file_path.read_bytes()
resp2 = client.post(
    "/api/documents/upload",
    files={"file": ("sample_image.png", io.BytesIO(img_bytes), "image/png")},
    data={"language": "en"},
)
assert resp2.status_code == 201, f"Expected 201, got {resp2.status_code}: {resp2.text}"
doc2 = resp2.json()
print(f"Uploaded English Image ID: {doc2['id']}, Status: {doc2['status']}, Confidence: {doc2['overall_confidence']}")
assert doc2["status"] == "completed"
assert doc2["overall_confidence"] > 80.0


print("\n==================================================")
print("TEST 3: Hindi PNG Document (language='hi')")
print("==================================================")
hindi_text = "यह एक परीक्षण दस्तावेज़ है।"
hindi_img_buf = create_indic_image(hindi_text)
resp3 = client.post(
    "/api/documents/upload",
    files={"file": ("hindi_sample.png", hindi_img_buf, "image/png")},
    data={"language": "hi"},
)
assert resp3.status_code == 201, f"Expected 201, got {resp3.status_code}: {resp3.text}"
doc3 = resp3.json()
print(f"Uploaded Hindi Image ID: {doc3['id']}, Status: {doc3['status']}, Confidence: {doc3['overall_confidence']}")

detail3 = client.get(f"/api/documents/{doc3['id']}").json()
assert len(detail3.get("ocr_results", [])) > 0
hindi_ocr_res = detail3["ocr_results"][0]
print(f"Hindi OCR Engine: {hindi_ocr_res.get('ocr_engine')}")
print(f"Hindi Extracted Text: {repr(hindi_ocr_res.get('extracted_text'))}")
print(f"Hindi OCR Confidence: {hindi_ocr_res.get('confidence')}")

# Verify Devanagari Unicode characters are present
extracted_hi = hindi_ocr_res.get("extracted_text", "")
devanagari_chars_hi = [c for c in extracted_hi if "\u0900" <= c <= "\u097F"]
print(f"Hindi Devanagari Characters Count: {len(devanagari_chars_hi)}")
assert len(devanagari_chars_hi) > 0, "Expected Devanagari characters in Hindi OCR result!"
assert hindi_ocr_res.get("ocr_engine") == "EasyOCR-Hindi"


print("\n==================================================")
print("TEST 4: Hindi Scanned PDF (language='hi')")
print("==================================================")
hindi_pdf_buf = create_scanned_indic_pdf(hindi_text)
resp4 = client.post(
    "/api/documents/upload",
    files={"file": ("hindi_scanned.pdf", hindi_pdf_buf, "application/pdf")},
    data={"language": "hi"},
)
assert resp4.status_code == 201, f"Expected 201, got {resp4.status_code}: {resp4.text}"
doc4 = resp4.json()
print(f"Uploaded Hindi Scanned PDF ID: {doc4['id']}, Status: {doc4['status']}")

detail4 = client.get(f"/api/documents/{doc4['id']}").json()
print(f"Hindi PDF Pages: {len(detail4.get('pages', []))}")
assert len(detail4.get("pages", [])) == 2
for p_idx, ocr_item in enumerate(detail4.get("ocr_results", [])):
    print(f"Page {p_idx + 1} OCR Engine: {ocr_item.get('ocr_engine')}, Confidence: {ocr_item.get('confidence')}")
    print(f"Page {p_idx + 1} Text: {repr(ocr_item.get('extracted_text'))}")
    assert any("\u0900" <= c <= "\u097F" for c in ocr_item.get("extracted_text", "")), f"Expected Devanagari on page {p_idx + 1}"


print("\n==================================================")
print("TEST 5: Marathi PNG Document (language='mr')")
print("==================================================")
marathi_text = "हा एक चाचणी दस्तऐवज आहे."
marathi_img_buf = create_indic_image(marathi_text)
resp5 = client.post(
    "/api/documents/upload",
    files={"file": ("marathi_sample.png", marathi_img_buf, "image/png")},
    data={"language": "mr"},
)
assert resp5.status_code == 201, f"Expected 201, got {resp5.status_code}: {resp5.text}"
doc5 = resp5.json()
print(f"Uploaded Marathi Image ID: {doc5['id']}, Status: {doc5['status']}, Confidence: {doc5['overall_confidence']}")

detail5 = client.get(f"/api/documents/{doc5['id']}").json()
assert len(detail5.get("ocr_results", [])) > 0
marathi_ocr_res = detail5["ocr_results"][0]
print(f"Marathi OCR Engine: {marathi_ocr_res.get('ocr_engine')}")
print(f"Marathi Extracted Text: {repr(marathi_ocr_res.get('extracted_text'))}")
print(f"Marathi OCR Confidence: {marathi_ocr_res.get('confidence')}")

# Verify Devanagari Unicode characters are present
extracted_mr = marathi_ocr_res.get("extracted_text", "")
devanagari_chars_mr = [c for c in extracted_mr if "\u0900" <= c <= "\u097F"]
print(f"Marathi Devanagari Characters Count: {len(devanagari_chars_mr)}")
assert len(devanagari_chars_mr) > 0, "Expected Devanagari characters in Marathi OCR result!"
assert marathi_ocr_res.get("ocr_engine") == "EasyOCR-Marathi"


print("\n==================================================")
print("TEST 6: Marathi Scanned PDF (language='mr')")
print("==================================================")
marathi_pdf_buf = create_scanned_indic_pdf(marathi_text)
resp6 = client.post(
    "/api/documents/upload",
    files={"file": ("marathi_scanned.pdf", marathi_pdf_buf, "application/pdf")},
    data={"language": "mr"},
)
assert resp6.status_code == 201, f"Expected 201, got {resp6.status_code}: {resp6.text}"
doc6 = resp6.json()
print(f"Uploaded Marathi Scanned PDF ID: {doc6['id']}, Status: {doc6['status']}")

detail6 = client.get(f"/api/documents/{doc6['id']}").json()
print(f"Marathi PDF Pages: {len(detail6.get('pages', []))}")
assert len(detail6.get("pages", [])) == 2
for p_idx, ocr_item in enumerate(detail6.get("ocr_results", [])):
    print(f"Page {p_idx + 1} OCR Engine: {ocr_item.get('ocr_engine')}, Confidence: {ocr_item.get('confidence')}")
    print(f"Page {p_idx + 1} Text: {repr(ocr_item.get('extracted_text'))}")
    assert any("\u0900" <= c <= "\u097F" for c in ocr_item.get("extracted_text", "")), f"Expected Devanagari on page {p_idx + 1}"


print("\n==================================================")
print("TEST 7: Invalid Language Validation")
print("==================================================")
resp7 = client.post(
    "/api/documents/upload",
    files={"file": ("test.png", io.BytesIO(b"dummy"), "image/png")},
    data={"language": "ta"},
)
print(f"Invalid language status code: {resp7.status_code}")
print(f"Invalid language detail: {resp7.json()}")
assert resp7.status_code == 400
assert "Unsupported language 'ta'" in resp7.json().get("detail", "")


print("\n==================================================")
print("TEST 8: Unsupported File Type Rejection")
print("==================================================")
resp8 = client.post(
    "/api/documents/upload",
    files={"file": ("malicious.exe", io.BytesIO(b"dummy binary"), "application/x-msdownload")},
    data={"language": "en"},
)
print(f"Unsupported file status code: {resp8.status_code}")
print(f"Unsupported file detail: {resp8.json()}")
assert resp8.status_code == 400


print("\n==================================================")
print("TEST 9: PostgreSQL Direct Database Verification")
print("==================================================")
with SessionLocal() as db:
    # Check Hindi DB record
    hi_doc = db.query(Document).filter(Document.id == doc3["id"]).first()
    assert hi_doc is not None
    assert len(hi_doc.ocr_results) > 0
    db_hi_ocr = hi_doc.ocr_results[0]
    print(f"DB Hindi OCR Record - ID: {db_hi_ocr.id}, Engine: {db_hi_ocr.ocr_engine}, Confidence: {db_hi_ocr.confidence}")
    print(f"DB Hindi Extracted Text: {repr(db_hi_ocr.extracted_text)}")
    assert db_hi_ocr.extracted_text is not None and len(db_hi_ocr.extracted_text) > 0
    assert any("\u0900" <= c <= "\u097F" for c in db_hi_ocr.extracted_text)

    # Check Marathi DB record
    mr_doc = db.query(Document).filter(Document.id == doc5["id"]).first()
    assert mr_doc is not None
    assert len(mr_doc.ocr_results) > 0
    db_mr_ocr = mr_doc.ocr_results[0]
    print(f"DB Marathi OCR Record - ID: {db_mr_ocr.id}, Engine: {db_mr_ocr.ocr_engine}, Confidence: {db_mr_ocr.confidence}")
    print(f"DB Marathi Extracted Text: {repr(db_mr_ocr.extracted_text)}")
    assert db_mr_ocr.extracted_text is not None and len(db_mr_ocr.extracted_text) > 0
    assert any("\u0900" <= c <= "\u097F" for c in db_mr_ocr.extracted_text)

print("\n==================================================")
print("ALL 9 REGIONAL & ENGLISH OCR TESTS PASSED SUCCESSFULLY!")
print("==================================================")

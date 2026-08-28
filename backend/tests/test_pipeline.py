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
from app.models.document import Document, DocumentPage, OCRResult, ExtractedField

BACKEND_DIR = Path(__file__).resolve().parents[1]
UPLOADS_DIR = BACKEND_DIR / "uploads"

client = TestClient(app)


# Helper to create clean test images with proper Devanagari typography
def create_indic_image(text: str, width: int = 800, height: int = 120) -> io.BytesIO:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    font_path = "C:/Windows/Fonts/Nirmala.ttc"
    if Path(font_path).exists():
        font = fitz.Font(fontfile=font_path)
        page.insert_font(fontname="Nirmala", fontbuffer=font.buffer)
        page.insert_textbox(fitz.Rect(20, 20, width - 20, height - 20), text, fontname="Nirmala", fontsize=22)
    else:
        page.insert_textbox(fitz.Rect(20, 20, width - 20, height - 20), text, fontsize=22)
    pix = page.get_pixmap(dpi=200)
    img_bytes = pix.tobytes("png")
    doc.close()
    return io.BytesIO(img_bytes)


def create_mixed_image(lines: list[str], width: int = 800, height: int = 250) -> io.BytesIO:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    font_path = "C:/Windows/Fonts/Nirmala.ttc"
    if Path(font_path).exists():
        font = fitz.Font(fontfile=font_path)
        page.insert_font(fontname="Nirmala", fontbuffer=font.buffer)
        y = 20
        for line in lines:
            page.insert_textbox(fitz.Rect(20, y, width - 20, y + 40), line, fontname="Nirmala", fontsize=18)
            y += 45
    else:
        y = 20
        for line in lines:
            page.insert_textbox(fitz.Rect(20, y, width - 20, y + 40), line, fontsize=18)
            y += 45
    pix = page.get_pixmap(dpi=200)
    img_bytes = pix.tobytes("png")
    doc.close()
    return io.BytesIO(img_bytes)


def create_scanned_pdf(lines_per_page: list[list[str]]) -> io.BytesIO:
    doc_out = fitz.open()
    font_path = "C:/Windows/Fonts/Nirmala.ttc"
    font = fitz.Font(fontfile=font_path) if Path(font_path).exists() else None

    for page_lines in lines_per_page:
        doc_temp = fitz.open()
        page_temp = doc_temp.new_page(width=800, height=220)
        if font is not None:
            page_temp.insert_font(fontname="Nirmala", fontbuffer=font.buffer)
            y = 20
            for line in page_lines:
                page_temp.insert_textbox(fitz.Rect(20, y, 780, y + 40), line, fontname="Nirmala", fontsize=18)
                y += 45
        else:
            y = 20
            for line in page_lines:
                page_temp.insert_textbox(fitz.Rect(20, y, 780, y + 40), line, fontsize=18)
                y += 45
        pix = page_temp.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        doc_temp.close()

        # Add rasterized scanned page to output PDF
        out_page = doc_out.new_page(width=800, height=220)
        out_page.insert_image(out_page.rect, stream=img_bytes)

    pdf_buf = io.BytesIO()
    doc_out.save(pdf_buf)
    doc_out.close()
    pdf_buf.seek(0)
    return pdf_buf





print("==================================================")
print("TEST 1: English Multi-page PDF (Regression Test - No Language Parameter)")
print("==================================================")
pdf_file_path = UPLOADS_DIR / "a8893ca61e5e427baf943f4d7dfcac44.pdf"
pdf_bytes = pdf_file_path.read_bytes()
resp1 = client.post(
    "/api/documents/upload",
    files={"file": ("Application_Form_Test.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
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
print("TEST 2: English PNG Image (Regression Test - Auto-Detect)")
print("==================================================")
img_file_path = UPLOADS_DIR / "bd36b5e9c49148759966f2367e5ca7bc.png"
img_bytes = img_file_path.read_bytes()
resp2 = client.post(
    "/api/documents/upload",
    files={"file": ("sample_image.png", io.BytesIO(img_bytes), "image/png")},
)
assert resp2.status_code == 201, f"Expected 201, got {resp2.status_code}: {resp2.text}"
doc2 = resp2.json()
print(f"Uploaded English Image ID: {doc2['id']}, Status: {doc2['status']}, Confidence: {doc2['overall_confidence']}")
assert doc2["status"] == "completed"
assert doc2["overall_confidence"] > 80.0


print("\n==================================================")
print("TEST 3: Hindi PNG Document (Automatic Language Detection -> PaddleOCR)")
print("==================================================")
hindi_text = "यह एक परीक्षण दस्तावेज़ है और हम इसे सत्यापित कर रहे हैं।"
hindi_img_buf = create_indic_image(hindi_text)
resp3 = client.post(
    "/api/documents/upload",
    files={"file": ("hindi_sample.png", hindi_img_buf, "image/png")},
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

extracted_hi = hindi_ocr_res.get("extracted_text", "")
devanagari_chars_hi = [c for c in extracted_hi if "\u0900" <= c <= "\u097F"]
print(f"Hindi Devanagari Characters Count: {len(devanagari_chars_hi)}")
assert len(devanagari_chars_hi) >= 15, "Expected Devanagari characters in Hindi OCR result!"
assert "PaddleOCR" in hindi_ocr_res.get("ocr_engine", "")


print("\n==================================================")
print("TEST 4: Hindi Scanned PDF (Automatic Language Detection -> PaddleOCR)")
print("==================================================")
hindi_pdf_buf = create_scanned_pdf([
    ["यह एक परीक्षण दस्तावेज़ है।", "दिनांक: 15/08/2026"],
    ["नाम: अमित शर्मा", "पता: नई दिल्ली"]
])
resp4 = client.post(
    "/api/documents/upload",
    files={"file": ("hindi_scanned.pdf", hindi_pdf_buf, "application/pdf")},
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
print("TEST 5: Real Marathi Poem Image (Automatic Language Detection -> PaddleOCR)")
print("==================================================")
marathi_poem_path = UPLOADS_DIR / "623f9bc11f134fa3a84045c6afe8307b.jpg"
if marathi_poem_path.exists():
    poem_bytes = marathi_poem_path.read_bytes()
    resp5 = client.post(
        "/api/documents/upload",
        files={"file": ("marathi_poem.jpg", io.BytesIO(poem_bytes), "image/jpeg")},
    )
else:
    marathi_text = "हा एक चाचणी दस्तऐवज आहे. आम्ही येथे मराठी भाषेचा वापर करत आहोत."
    resp5 = client.post(
        "/api/documents/upload",
        files={"file": ("marathi_sample.png", create_indic_image(marathi_text), "image/png")},
    )

assert resp5.status_code == 201, f"Expected 201, got {resp5.status_code}: {resp5.text}"
doc5 = resp5.json()
print(f"Uploaded Marathi Image ID: {doc5['id']}, Status: {doc5['status']}, Confidence: {doc5['overall_confidence']}")

detail5 = client.get(f"/api/documents/{doc5['id']}").json()
assert len(detail5.get("ocr_results", [])) > 0
marathi_ocr_res = detail5["ocr_results"][0]
print(f"Marathi OCR Engine: {marathi_ocr_res.get('ocr_engine')}")
print(f"Marathi Extracted Text:\n{marathi_ocr_res.get('extracted_text')}")
print(f"Marathi OCR Confidence: {marathi_ocr_res.get('confidence')}")

extracted_mr = marathi_ocr_res.get("extracted_text", "")
devanagari_chars_mr = [c for c in extracted_mr if "\u0900" <= c <= "\u097F"]
print(f"Marathi Devanagari Characters Count: {len(devanagari_chars_mr)}")
assert len(devanagari_chars_mr) >= 15, "Expected Devanagari characters in Marathi OCR result!"
assert "PaddleOCR" in marathi_ocr_res.get("ocr_engine", "")
assert marathi_ocr_res.get("confidence", 0) >= 80.0, f"Expected PaddleOCR high confidence, got {marathi_ocr_res.get('confidence')}"


print("\n==================================================")
print("TEST 6: Marathi Scanned PDF (Automatic Language Detection -> PaddleOCR)")
print("==================================================")
marathi_pdf_buf = create_scanned_pdf([
    ["हा एक चाचणी दस्तऐवज आहे.", "दिनांक: 28/08/2026"],
    ["नाव: सागर पोतदार", "पत्ता: मुंबई, महाराष्ट्र"]
])
resp6 = client.post(
    "/api/documents/upload",
    files={"file": ("marathi_scanned.pdf", marathi_pdf_buf, "application/pdf")},
)
assert resp6.status_code == 201, f"Expected 201, got {resp6.status_code}: {resp6.text}"
doc6 = resp6.json()
print(f"Uploaded Marathi Scanned PDF ID: {doc6['id']}, Status: {doc6['status']}")

detail6 = client.get(f"/api/documents/{doc6['id']}").json()
assert len(detail6.get("pages", [])) == 2
for p_idx, ocr_item in enumerate(detail6.get("ocr_results", [])):
    print(f"Page {p_idx + 1} OCR Engine: {ocr_item.get('ocr_engine')}, Confidence: {ocr_item.get('confidence')}")
    print(f"Page {p_idx + 1} Text: {repr(ocr_item.get('extracted_text'))}")
    assert any("\u0900" <= c <= "\u097F" for c in ocr_item.get("extracted_text", "")), f"Expected Devanagari on page {p_idx + 1}"


print("\n==================================================")
print("TEST 7: English + Hindi Mixed Document (en+hi Auto-Detect)")
print("==================================================")
mixed_hi_lines = [
    "Name: Rhea Varghese",
    "पता: नई दिल्ली, भारत",
    "यह एक परीक्षण दस्तावेज़ है।",
    "Status: Verified successfully"
]
mixed_hi_buf = create_mixed_image(mixed_hi_lines)
resp7 = client.post(
    "/api/documents/upload",
    files={"file": ("mixed_hindi.png", mixed_hi_buf, "image/png")},
)
assert resp7.status_code == 201, f"Expected 201, got {resp7.status_code}: {resp7.text}"
doc7 = resp7.json()
print(f"Uploaded Mixed Hi ID: {doc7['id']}, Status: {doc7['status']}, Confidence: {doc7['overall_confidence']}")

detail7 = client.get(f"/api/documents/{doc7['id']}").json()
ocr_res7 = detail7["ocr_results"][0]
print(f"Mixed Hi OCR Engine: {ocr_res7.get('ocr_engine')}")
print(f"Mixed Hi Extracted Text:\n{ocr_res7.get('extracted_text')}")
assert "PaddleOCR" in ocr_res7.get("ocr_engine", "")
assert "Rhea" in ocr_res7.get("extracted_text", "")
assert any("\u0900" <= c <= "\u097F" for c in ocr_res7.get("extracted_text", ""))


print("\n==================================================")
print("TEST 8: English + Marathi Mixed Document (en+mr Auto-Detect)")
print("==================================================")
mixed_mr_lines = [
    "Name: Rhea Varghese",
    "पत्ता: मुंबई, महाराष्ट्र",
    "हा एक चाचणी दस्तऐवज आहे.",
    "Status: Verified successfully"
]
mixed_mr_buf = create_mixed_image(mixed_mr_lines)
resp8 = client.post(
    "/api/documents/upload",
    files={"file": ("mixed_marathi.png", mixed_mr_buf, "image/png")},
)
assert resp8.status_code == 201, f"Expected 201, got {resp8.status_code}: {resp8.text}"
doc8 = resp8.json()
print(f"Uploaded Mixed Mr ID: {doc8['id']}, Status: {doc8['status']}, Confidence: {doc8['overall_confidence']}")

detail8 = client.get(f"/api/documents/{doc8['id']}").json()
ocr_res8 = detail8["ocr_results"][0]
print(f"Mixed Mr OCR Engine: {ocr_res8.get('ocr_engine')}")
print(f"Mixed Mr Extracted Text:\n{ocr_res8.get('extracted_text')}")
assert "PaddleOCR" in ocr_res8.get("ocr_engine", "")
assert "Rhea" in ocr_res8.get("extracted_text", "")
assert any("\u0900" <= c <= "\u097F" for c in ocr_res8.get("extracted_text", ""))


print("\n==================================================")
print("TEST 9: Unsupported File Type Rejection")
print("==================================================")
resp9 = client.post(
    "/api/documents/upload",
    files={"file": ("malicious.exe", io.BytesIO(b"dummy binary"), "application/x-msdownload")},
)
print(f"Unsupported file status code: {resp9.status_code}")
assert resp9.status_code == 400


print("\n==================================================")
print("TEST 10: PostgreSQL Direct Database Unicode Integrity")
print("==================================================")
with SessionLocal() as db:
    hi_doc = db.query(Document).filter(Document.id == doc3["id"]).first()
    assert hi_doc is not None
    assert len(hi_doc.ocr_results) > 0
    db_hi_ocr = hi_doc.ocr_results[0]
    print(f"DB Hindi OCR Record - ID: {db_hi_ocr.id}, Engine: {db_hi_ocr.ocr_engine}, Confidence: {db_hi_ocr.confidence}")
    print(f"DB Hindi Extracted Text: {repr(db_hi_ocr.extracted_text)}")
    assert any("\u0900" <= c <= "\u097F" for c in db_hi_ocr.extracted_text)

    mr_doc = db.query(Document).filter(Document.id == doc5["id"]).first()
    assert mr_doc is not None
    assert len(mr_doc.ocr_results) > 0
    db_mr_ocr = mr_doc.ocr_results[0]
    print(f"DB Marathi OCR Record - ID: {db_mr_ocr.id}, Engine: {db_mr_ocr.ocr_engine}, Confidence: {db_mr_ocr.confidence}")
    print(f"DB Marathi Extracted Text: {repr(db_mr_ocr.extracted_text)}")
    assert any("\u0900" <= c <= "\u097F" for c in db_mr_ocr.extracted_text)

print("\n==================================================")
print("ALL 10 PADDLEOCR, MIXED, REGIONAL & ENGLISH OCR TESTS PASSED SUCCESSFULLY!")
print("==================================================")

import io
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app

from PIL import Image, ImageDraw

BACKEND_DIR = Path(__file__).resolve().parents[1]
UPLOADS_DIR = BACKEND_DIR / "uploads"

client = TestClient(app)

print('=== Test 1: Upload Multi-page PDF ===')
pdf_file_path = UPLOADS_DIR / 'a8893ca61e5e427baf943f4d7dfcac44.pdf'
pdf_bytes = pdf_file_path.read_bytes()
resp1 = client.post(
    '/api/documents/upload',
    files={'file': ('Application_Form_Test.pdf', io.BytesIO(pdf_bytes), 'application/pdf')}
)
print('Upload PDF status code:', resp1.status_code)
doc1_data = resp1.json()
print('PDF Response:', doc1_data)
doc1_id = doc1_data['id']

print('\n=== Test 2: Get Document Detail for PDF ===')
resp2 = client.get(f'/api/documents/{doc1_id}')
print('Get PDF Detail status code:', resp2.status_code)
detail1 = resp2.json()
print(f'Document ID: {detail1.get("id")}, Type: {detail1.get("document_type")}, Status: {detail1.get("status")}, Confidence: {detail1.get("overall_confidence")}')
print(f'Pages count: {len(detail1.get("pages", []))}')
print(f'OCR results count: {len(detail1.get("ocr_results", []))}')
print(f'Extracted fields count: {len(detail1.get("extracted_fields", []))}')
for f in detail1.get('extracted_fields', []):
    print(f'  - {f.get("field_name")}: {f.get("field_value")} (conf={f.get("confidence")}, page_id={f.get("page_id")})')

print('\n=== Test 3: Upload PNG Image ===')
img_file_path = UPLOADS_DIR / 'bd36b5e9c49148759966f2367e5ca7bc.png'
img_bytes = img_file_path.read_bytes()
resp3 = client.post(
    '/api/documents/upload',
    files={'file': ('sample_image.png', io.BytesIO(img_bytes), 'image/png')}
)

print('Upload Image status code:', resp3.status_code)
doc3_data = resp3.json()
print('Image Response:', doc3_data)
doc3_id = doc3_data['id']

print('\n=== Test 4: Upload Low-Confidence / Blank Document (Review Queue Test) ===')
blank_img = Image.new('RGB', (100, 100), color=(255, 255, 255))
buf = io.BytesIO()
blank_img.save(buf, format='PNG')
buf.seek(0)
resp4 = client.post(
    '/api/documents/upload',
    files={'file': ('blank_noise.png', buf, 'image/png')}
)
print('Upload Blank Image status code:', resp4.status_code)
doc4_data = resp4.json()
print('Blank Image Document Status:', doc4_data.get('status'), 'Confidence:', doc4_data.get('overall_confidence'))
doc4_id = doc4_data['id']

detail4 = client.get(f'/api/documents/{doc4_id}').json()
print(f'Review Queue Items count: {len(detail4.get("review_items", []))}')
if detail4.get('review_items'):
    print(f'Review Reason: {detail4["review_items"][0].get("reason")}')
    print(f'Review Item Status: {detail4["review_items"][0].get("status")}')

print('\n=== Test 5: Upload Unsupported File ===')
resp5 = client.post(
    '/api/documents/upload',
    files={'file': ('malicious.exe', io.BytesIO(b'dummy content'), 'application/x-msdownload')}
)
print('Unsupported file status code:', resp5.status_code)
print('Unsupported file response:', resp5.json())

print('\n=== Test 6: List Documents ===')
resp6 = client.get('/api/documents?limit=5')
print('List documents status code:', resp6.status_code)
print('List count:', len(resp6.json()))
print('\nALL PIPELINE TESTS COMPLETED SUCCESSFULLY!')

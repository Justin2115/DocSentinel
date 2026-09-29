from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import SessionLocal
from app.models.document import Document, OCRResult
from app.services.embedding_service import index_document_embeddings


def main() -> None:
    db = SessionLocal()
    try:
        documents = (
            db.query(Document)
            .join(OCRResult, OCRResult.document_id == Document.id)
            .distinct()
            .all()
        )
        indexed = 0
        for document in documents:
            count = index_document_embeddings(db, document.id)
            if count:
                if document.status not in ("needs_review", "failed"):
                    document.status = "indexed"
                    db.commit()
                indexed += 1
                print(f"Indexed document {document.id} ({count} chunks)")
            else:
                print(f"Skipped document {document.id} (no text chunks)")
        print(f"Done. Indexed {indexed} of {len(documents)} documents.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

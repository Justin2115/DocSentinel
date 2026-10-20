#!/usr/bin/env python3
"""Rebuild Chroma embeddings from the existing OCR rows."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import exists

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.session import SessionLocal
from app.models.document import Document, OCRResult
from app.services.embedding_service import index_document_embeddings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document-id",
        type=int,
        help="Reindex one document; otherwise reindex all documents with OCR text",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    db = SessionLocal()
    failures = 0
    try:
        query = db.query(Document).filter(
            exists().where(OCRResult.document_id == Document.id)
        )
        if args.document_id is not None:
            query = query.filter(Document.id == args.document_id)
        documents = query.order_by(Document.id).all()
        if args.document_id is not None and not documents:
            logging.error("Document %s does not exist", args.document_id)
            return 2
        for document in documents:
            try:
                count = index_document_embeddings(db, document.id)
                logging.info("Document %s: indexed %s chunks", document.id, count)
            except Exception:
                failures += 1
                db.rollback()
                logging.exception("Document %s reindex failed", document.id)
    finally:
        db.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

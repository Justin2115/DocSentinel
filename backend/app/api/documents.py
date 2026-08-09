from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.document import Document
from app.schemas.document import DocumentResponse
from app.services.upload_service import save_uploaded_file


router = APIRouter(
    prefix="/api/documents",
    tags=["Documents"],
)


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=201,
)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        (
            original_filename,
            stored_filename,
            file_size,
        ) = await save_uploaded_file(file)

        file_path = str(
            Path("uploads") / stored_filename
        )

        document = Document(
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=file_path,
            file_type=file.content_type
            or "application/octet-stream",
            file_size=file_size,
            status="uploaded",
        )

        db.add(document)
        db.commit()
        db.refresh(document)

        return document

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:
        db.rollback()

        print("Document upload error:", error)

        raise HTTPException(
            status_code=500,
            detail="Failed to upload document.",
        )

    finally:
        await file.close()
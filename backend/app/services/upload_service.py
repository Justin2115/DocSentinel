from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


BASE_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".jpg",
    ".jpeg",
    ".png",
    ".xlsx",
    ".xls",
}

MAX_FILE_SIZE = 25 * 1024 * 1024


async def save_uploaded_file(
    file: UploadFile,
) -> tuple[str, str, int]:

    original_filename = file.filename or "uploaded_file"

    extension = Path(
        original_filename
    ).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(
            "Unsupported file type."
        )

    stored_filename = (
        f"{uuid4().hex}{extension}"
    )

    destination = UPLOAD_DIR / stored_filename

    file_size = 0

    with destination.open("wb") as buffer:

        while chunk := await file.read(1024 * 1024):

            file_size += len(chunk)

            if file_size > MAX_FILE_SIZE:
                destination.unlink(
                    missing_ok=True
                )

                raise ValueError(
                    "File size exceeds 25 MB."
                )

            buffer.write(chunk)

    return (
        original_filename,
        stored_filename,
        file_size,
    )
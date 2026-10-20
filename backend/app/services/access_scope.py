"""Permission-scoped document selection shared by search and RAG."""

from sqlalchemy.orm import Session

from app.core.rbac import can_view_document
from app.models.document import Document
from app.models.user import User
from app.services.admin_service import check_folder_permission


def authorized_document_ids(db: Session, user: User) -> list[int]:
    """Return documents this user can view, including configured folder rules."""
    documents = db.query(Document).all()
    allowed: list[int] = []
    for document in documents:
        if not can_view_document(user, document):
            continue
        folder = document.document_type or document.department
        if folder and not check_folder_permission(db, folder, user.role, action="view"):
            continue
        allowed.append(document.id)
    return allowed

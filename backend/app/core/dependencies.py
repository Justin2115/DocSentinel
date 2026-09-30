from typing import Optional, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

security = HTTPBearer(auto_error=False)


async def get_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Dependency that returns the current authenticated user or raises 401."""
    if not auth or not auth.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(auth.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user identifier in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    return user


from app.core.rbac import UserRole, can_review_document, can_view_document


async def get_optional_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Dependency that returns the current authenticated user if valid token present, otherwise None."""
    if not auth or not auth.credentials:
        return None
    payload = decode_access_token(auth.credentials)
    if not payload:
        return None
    user_id_str = payload.get("sub")
    if not user_id_str:
        return None
    try:
        user_id = int(user_id_str)
    except ValueError:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        return None
    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces admin role access."""
    user_role = UserRole.normalize(current_user.role)
    if user_role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Admin privileges required",
        )
    return current_user


async def require_upload_maker(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces upload maker or admin access."""
    user_role = UserRole.normalize(current_user.role)
    if user_role not in (UserRole.UPLOAD_MAKER.value, UserRole.ADMIN.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Upload Maker privileges required",
        )
    return current_user


async def require_upload_checker(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces upload checker or admin access."""
    user_role = UserRole.normalize(current_user.role)
    if user_role not in (UserRole.UPLOAD_CHECKER.value, UserRole.ADMIN.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Upload Checker privileges required",
        )
    return current_user


def verify_document_view_access(document: Any, user: User, db: Optional[Session] = None) -> None:
    """Check that user has permission to view the given document."""
    if not can_view_document(user, document):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: You do not have permission to view documents in this department",
        )
    if db is not None:
        from app.services.admin_service import check_folder_permission
        doc_folder = getattr(document, "document_type", None) or getattr(document, "department", None)
        if doc_folder and not check_folder_permission(db, doc_folder, user.role, action="view"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: Role '{user.role}' does not have view permission for '{doc_folder}' documents",
            )



def verify_document_review_access(
    document: Any, user: User, db: Optional[Session] = None
) -> None:
    """Check that user has permission to review the document and enforce separation of duties."""
    allowed, error_msg = can_review_document(user, document)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access forbidden: {error_msg}",
        )
    if db is not None:
        from app.services.admin_service import check_folder_permission
        doc_folder = getattr(document, "document_type", None) or getattr(document, "department", None)
        if doc_folder and not check_folder_permission(db, doc_folder, user.role, action="review"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: Role '{user.role}' does not have review permission for '{doc_folder}' documents",
            )


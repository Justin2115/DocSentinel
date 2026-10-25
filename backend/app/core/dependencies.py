from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.rbac import UserRole, can_review_document, can_view_document
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.services.audit_service import log_activity

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


async def require_super_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces Super Admin role access."""
    if not current_user.is_super_admin:
        log_activity(
            action="UNAUTHORIZED_ACCESS",
            status="FAILURE",
            actor_id=current_user.id,
            username=current_user.email,
            department=current_user.department,
            role=current_user.role,
            target_resource="super_admin_area",
            failure_reason="Super Admin privileges required",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Super Admin privileges required",
        )
    return current_user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces administrative role access (Super Admin or Department Admin)."""
    if not current_user.is_admin:
        log_activity(
            action="UNAUTHORIZED_ACCESS",
            status="FAILURE",
            actor_id=current_user.id,
            username=current_user.email,
            department=current_user.department,
            role=current_user.role,
            target_resource="admin_area",
            failure_reason="Admin privileges required",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Admin privileges required",
        )
    return current_user


async def require_dept_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces Department Admin or Super Admin access."""
    if not (current_user.is_dept_admin or current_user.is_super_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Department Admin privileges required",
        )
    return current_user


async def require_upload_maker(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces upload maker, dept admin, or super admin access."""
    if not current_user.is_maker:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Upload Maker privileges required",
        )
    return current_user


async def require_upload_checker(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that enforces upload checker, dept admin, or super admin access. Strictly blocks makers."""
    if current_user.is_strict_maker:
        log_activity(
            action="UNAUTHORIZED_ACCESS",
            status="FAILURE",
            actor_id=current_user.id,
            username=current_user.email,
            department=current_user.department,
            role=current_user.role,
            target_resource="review_queue",
            failure_reason="Makers cannot access the Review Queue or verify documents",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Department Makers cannot access the Review Queue or verify documents",
        )
    if not current_user.is_checker:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Upload Checker privileges required",
        )
    return current_user


def verify_document_view_access(document: Any, user: User, db: Optional[Session] = None) -> None:
    """Check that user has permission to view the given document."""
    if not can_view_document(user, document):
        doc_id = getattr(document, "id", None)
        log_activity(
            action="UNAUTHORIZED_ACCESS",
            status="FAILURE",
            actor_id=getattr(user, "id", None),
            username=getattr(user, "email", None),
            department=getattr(user, "department", None),
            role=getattr(user, "role", None),
            target_resource=f"document:{doc_id}" if doc_id else "document",
            failure_reason="Cross-department document access forbidden",
        )
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
        doc_id = getattr(document, "id", None)
        log_activity(
            action="UNAUTHORIZED_ACCESS",
            status="FAILURE",
            actor_id=getattr(user, "id", None),
            username=getattr(user, "email", None),
            department=getattr(user, "department", None),
            role=getattr(user, "role", None),
            target_resource=f"document:{doc_id}" if doc_id else "document",
            failure_reason=error_msg,
        )
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

import logging
import re
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.core.rbac import Department, UserRole
from app.core.security import hash_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.admin import (
    AddWorkflowRuleRequest,
    FolderPermissionMatrixItem,
    FolderPermissionsUpdateRequest,
    WorkflowConfigRequest,
    WorkflowConfigResponse,
    WorkflowRuleItem,
)
from app.schemas.auth import MessageResponse, UserResponse
from app.schemas.user import UserInviteRequest, UserUpdateRequest
from app.services.admin_service import (
    add_workflow_rule,
    delete_workflow_rule,
    get_folder_permissions_matrix,
    get_workflow_configuration,
    update_folder_permissions_matrix,
    update_workflow_configuration,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin Management"],
)


# ============================================================
# USERS & ROLES
# ============================================================

@router.get(
    "/users",
    response_model=List[UserResponse],
    summary="Admin only: List all users in the system",
)
async def list_users(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Retrieve all users in the database with their current roles and statuses."""
    users = db.query(User).order_by(User.id.asc()).all()
    return [UserResponse.model_validate(u) for u in users]


@router.post(
    "/users/invite",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin only: Invite or create a new team member",
)
async def invite_user(
    payload: UserInviteRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Invite a new team member with specified role and optional department.
    Validates input and ensures no duplicate email exists.
    """
    clean_email = payload.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid email address is required",
        )

    clean_name = payload.name.strip()
    if not clean_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User name is required",
        )

    # Validate and normalize role before duplicate/reactivation handling.
    normalized_role = UserRole.parse_assignment(payload.role)
    if normalized_role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Supported roles are: Admin, Upload Maker, Upload Checker",
        )

    normalized_dept = None
    if payload.department and payload.department.strip():
        normalized_dept = Department.normalize(payload.department)
        if normalized_dept not in {department.value for department in Department}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid department '{payload.department}'",
            )

    # Check for existing user with duplicate email
    existing_user = db.query(User).filter(User.email == clean_email).first()
    if existing_user:
        if not existing_user.is_active:
            # Reactivate previously deactivated user with updated details
            existing_user.is_active = True
            existing_user.name = clean_name
            existing_user.role = normalized_role
            if normalized_dept:
                existing_user.department = normalized_dept
            db.commit()
            db.refresh(existing_user)
            logger.info("Reactivated previously deactivated user %s", clean_email)
            return UserResponse.model_validate(existing_user)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A user with email '{clean_email}' already exists",
        )

    # Invited users can sign in through Google OAuth; never assign a shared password.
    pw_hash = hash_password(payload.password) if payload.password else None

    new_user = User(
        name=clean_name,
        email=clean_email,
        password_hash=pw_hash,
        role=normalized_role,
        department=normalized_dept,
        is_active=True,
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A user with email '{clean_email}' already exists",
        ) from error
    db.refresh(new_user)
    logger.info("Admin %s invited new user %s as %s", current_user.email, clean_email, normalized_role)
    return UserResponse.model_validate(new_user)


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Admin only: Update a user's role, department, or active status",
)
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found",
        )

    # Prevent admin from demoting their own role
    if payload.role is not None and current_user.id == target_user.id:
        normalized_requested = UserRole.normalize(payload.role)
        if normalized_requested != UserRole.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Administrators cannot demote their own account role",
            )

    if payload.role is not None:
        normalized_role = UserRole.parse_assignment(payload.role)
        if normalized_role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role '{payload.role}'. Must be one of: Admin, Upload Maker, Upload Checker",
            )
        target_user.role = normalized_role

    if payload.department is not None:
        if payload.department.strip() == "" or payload.department.upper() == "NONE":
            target_user.department = None
        else:
            normalized_dept = Department.normalize(payload.department)
            target_user.department = normalized_dept

    if payload.is_active is not None:
        if current_user.id == target_user.id and not payload.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Administrators cannot deactivate their own account",
            )
        target_user.is_active = payload.is_active

    if payload.name is not None and payload.name.strip():
        target_user.name = payload.name.strip()

    db.commit()
    db.refresh(target_user)
    return UserResponse.model_validate(target_user)


@router.patch(
    "/users/{user_id}/role",
    response_model=UserResponse,
    summary="Admin only: Update a user's role specifically",
)
async def update_user_role(
    user_id: int,
    payload: dict,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    role_val = payload.get("role")
    if not role_val:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role field is required",
        )
    return await update_user(
        user_id=user_id,
        payload=UserUpdateRequest(role=role_val),
        current_user=current_user,
        db=db,
    )


@router.delete(
    "/users/{user_id}",
    response_model=MessageResponse,
    summary="Admin only: Deactivate/remove a member",
)
async def delete_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Safely deactivate a member so document ownership and audit history are preserved.
    Prevents an admin from deleting their own active session.
    """
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own active administrator account",
        )

    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found",
        )

    # Safe deactivation
    target_user.is_active = False
    db.commit()
    logger.info("Admin %s deactivated user %s (#%s)", current_user.email, target_user.email, user_id)
    return MessageResponse(message=f"Member '{target_user.name}' has been deactivated successfully")


# ============================================================
# PERMISSIONS MATRIX
# ============================================================

@router.get(
    "/permissions",
    response_model=List[FolderPermissionMatrixItem],
    summary="Admin only: Get folder access permissions matrix",
)
async def get_permissions(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Retrieve the current folder access matrix for all roles."""
    return get_folder_permissions_matrix(db)


@router.put(
    "/permissions",
    response_model=List[FolderPermissionMatrixItem],
    summary="Admin only: Update folder access permissions matrix",
)
async def update_permissions(
    payload: List[FolderPermissionMatrixItem],
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save changes to the folder access permissions matrix."""
    dict_list = [item.model_dump() for item in payload]
    updated = update_folder_permissions_matrix(db, dict_list)
    return updated


# ============================================================
# WORKFLOW RULES
# ============================================================

@router.get(
    "/workflow",
    response_model=WorkflowConfigResponse,
    summary="Admin only: Get workflow confidence threshold and routing rules",
)
async def get_workflow(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Retrieve the auto-flag threshold and active routing rules."""
    return get_workflow_configuration(db)


@router.put(
    "/workflow",
    response_model=WorkflowConfigResponse,
    summary="Admin only: Update workflow confidence threshold and rules",
)
async def update_workflow(
    payload: WorkflowConfigRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update confidence threshold and sync workflow routing rules."""
    rules_dict = (
        [r.model_dump() for r in payload.rules] if payload.rules is not None else None
    )
    updated = update_workflow_configuration(db, payload.threshold, rules_dict)
    return updated


@router.post(
    "/workflow/rules",
    response_model=WorkflowRuleItem,
    status_code=status.HTTP_201_CREATED,
    summary="Admin only: Add a new routing rule",
)
async def create_workflow_rule(
    payload: AddWorkflowRuleRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Add a new document routing automation rule."""
    created = add_workflow_rule(
        db,
        title=payload.title,
        route=payload.route,
        rule_type=payload.type,
        threshold=payload.threshold,
        document_type=payload.document_type,
    )
    return created


@router.delete(
    "/workflow/rules/{rule_id}",
    response_model=MessageResponse,
    summary="Admin only: Delete a routing rule",
)
async def remove_workflow_rule(
    rule_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove a document routing rule."""
    success = delete_workflow_rule(db, rule_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow rule with ID {rule_id} not found",
        )
    return MessageResponse(message="Routing rule removed successfully")

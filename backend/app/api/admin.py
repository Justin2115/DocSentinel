from datetime import datetime
import logging
import math
import re
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin, require_super_admin
from app.core.rbac import (
    Department,
    UserRole,
    WORKFLOW_CONDITION_CONFIDENCE_BELOW,
    WORKFLOW_RULE_TYPES,
    can_manage_workflow_document_type,
    canonical_document_type,
    department_for_document_type,
    managed_workflow_departments,
    normalize_known_department,
    resolve_workflow_scope_department,
)
from app.core.security import hash_password
from app.db.session import get_db
from app.models.audit import ActivityLog
from app.models.document import Document, ReviewQueue
from app.models.user import User
from app.schemas.admin import (
    ActivityLogListResponse,
    ActivityLogResponse,
    AddWorkflowRuleRequest,
    FolderPermissionMatrixItem,
    FolderPermissionsUpdateRequest,
    WorkflowConfigRequest,
    WorkflowConfigResponse,
    WorkflowOptionsResponse,
    WorkflowRuleItem,
)
from app.schemas.auth import MessageResponse, UserResponse
from app.schemas.user import UserInviteRequest, UserUpdateRequest
from app.services.admin_service import (
    add_workflow_rule,
    delete_workflow_rule,
    get_folder_permissions_matrix,
    get_workflow_configuration,
    get_workflow_options,
    is_eligible_route,
    rule_belongs_to_department,
    update_folder_permissions_matrix,
    update_workflow_configuration,
)
from app.services.audit_service import log_activity

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin Management"],
)


def ensure_single_active_super_admin(db: Session, target_user_id: int | None = None) -> None:
    """Enforce the single active Super Admin rule in the database."""
    query = db.query(User).filter(
        User.role.in_([UserRole.SUPER_ADMIN.value, UserRole.ADMIN.value]),
        User.is_active == True,
    )
    if target_user_id is not None:
        query = query.filter(User.id != target_user_id)
    existing = query.first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only one active Super Admin account is permitted in the system.",
        )


# ============================================================
# USERS & ROLES
# ============================================================

@router.get(
    "/users",
    response_model=List[UserResponse],
    summary="Admin only: List users with department scoping",
)
async def list_users(
    department: Optional[str] = Query(None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Retrieve users:
    - Super Admin: sees all users across all departments, or filtered by department
    - Department Admin: sees only users belonging to their assigned department
    """
    query = db.query(User)
    # Guard against direct Python function calls where default Query param is passed
    dept_str = department if isinstance(department, str) else None

    if current_user.is_super_admin:
        if dept_str and dept_str.strip() and dept_str.upper() != "ALL":
            dept_clean = Department.normalize(dept_str)
            query = query.filter(func.upper(User.department) == dept_clean)
    else:
        admin_dept = (current_user.department or "").strip().upper()
        if dept_str and dept_str.strip() and Department.normalize(dept_str) != admin_dept:
            log_activity(
                action="UNAUTHORIZED_ACCESS",
                status="FAILURE",
                actor_id=current_user.id,
                username=current_user.email,
                department=current_user.department,
                role=current_user.role,
                target_resource="users",
                failure_reason="Department Admin attempted to view users from another department",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Department Admins can only view users within their assigned department",
            )
        query = query.filter(func.upper(User.department) == admin_dept)

    users = query.order_by(User.id.asc()).all()
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
    Invite a new team member.
    Enforces role escalation prevention, department isolation, and single Super Admin rule.
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

    normalized_role = UserRole.parse_assignment(payload.role)
    if normalized_role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Supported roles are: Super Admin, Department Admin, Department Checker, Department Maker",
        )

    # Department handling & Department Admin restriction
    normalized_dept = None
    if current_user.is_super_admin:
        if payload.department and payload.department.strip() and payload.department.upper() != "NONE":
            normalized_dept = Department.normalize(payload.department)
    else:
        # Department Admin can ONLY invite to their own department
        dept_admin_dept = (current_user.department or "").strip().upper()
        if payload.department and payload.department.strip() and payload.department.upper() != "NONE":
            req_dept = Department.normalize(payload.department)
            if req_dept != dept_admin_dept:
                log_activity(
                    action="UNAUTHORIZED_ACCESS",
                    status="FAILURE",
                    actor_id=current_user.id,
                    username=current_user.email,
                    department=current_user.department,
                    role=current_user.role,
                    target_resource="users:invite",
                    failure_reason="Department Admin cannot invite users to other departments",
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Department Admins can only invite users to their own department",
                )
        normalized_dept = dept_admin_dept

        # Prevent Department Admin from assigning Super Admin or another Dept Admin
        if UserRole.is_super_admin(normalized_role) or UserRole.is_dept_admin(normalized_role):
            log_activity(
                action="UNAUTHORIZED_ACCESS",
                status="FAILURE",
                actor_id=current_user.id,
                username=current_user.email,
                department=current_user.department,
                role=current_user.role,
                target_resource="users:invite",
                failure_reason="Department Admin attempted role escalation",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Department Admins cannot assign administrative roles",
            )

    # Mandatory department enforcement:
    # Super Admin can be system-wide (unassigned), but Department Admin, Checker, and Maker MUST have a valid department.
    if not UserRole.is_super_admin(normalized_role):
        if not normalized_dept:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid department must be selected for Department Admin, Department Checker, and Department Maker roles.",
            )

    pw_hash = hash_password(payload.password.strip()) if payload.password and payload.password.strip() else None

    # Check for existing user first
    existing_user = db.query(User).filter(User.email == clean_email).first()
    if existing_user and existing_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A user with email '{clean_email}' already exists",
        )

    # Enforce single active Super Admin
    if UserRole.is_super_admin(normalized_role):
        ensure_single_active_super_admin(db, target_user_id=existing_user.id if existing_user else None)

    if existing_user:
        # Reactivate previously deactivated user with updated details
        existing_user.is_active = True
        existing_user.name = clean_name
        existing_user.role = normalized_role
        if normalized_dept:
            existing_user.department = normalized_dept
        if pw_hash:
            existing_user.password_hash = pw_hash
        db.commit()
        db.refresh(existing_user)
        log_activity(
            action="USER_CREATE",
            status="SUCCESS",
            actor_id=current_user.id,
            username=current_user.email,
            department=current_user.department,
            role=current_user.role,
            target_resource=f"user:{existing_user.id}",
            details={"email": clean_email, "role": normalized_role, "reactivated": True},
        )
        return UserResponse.model_validate(existing_user)

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
            detail=f"A user with email '{clean_email}' already exists or violates constraint",
        ) from error

    db.refresh(new_user)
    log_activity(
        action="USER_CREATE",
        status="SUCCESS",
        actor_id=current_user.id,
        username=current_user.email,
        department=current_user.department,
        role=current_user.role,
        target_resource=f"user:{new_user.id}",
        details={"email": clean_email, "role": normalized_role, "department": normalized_dept},
    )
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

    # Department Admin restrictions
    if not current_user.is_super_admin:
        admin_dept = (current_user.department or "").strip().upper()
        target_dept = (target_user.department or "").strip().upper()
        if target_dept != admin_dept or target_user.is_super_admin:
            log_activity(
                action="UNAUTHORIZED_ACCESS",
                status="FAILURE",
                actor_id=current_user.id,
                username=current_user.email,
                department=current_user.department,
                role=current_user.role,
                target_resource=f"user:{user_id}",
                failure_reason="Department Admin cannot modify users outside their department",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Department Admins can only manage users within their assigned department",
            )
        if payload.department is not None and payload.department.strip() and payload.department.upper() != "NONE":
            new_dept = Department.normalize(payload.department)
            if new_dept != admin_dept:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Department Admins cannot reassign users to other departments",
                )
        if payload.role is not None:
            norm_new_role = UserRole.parse_assignment(payload.role)
            if UserRole.is_super_admin(norm_new_role) or UserRole.is_dept_admin(norm_new_role):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Department Admins cannot assign administrative roles",
                )

    # Prevent demoting / deactivating the only active Super Admin
    if target_user.is_super_admin:
        if payload.is_active is False or (payload.role and not UserRole.is_super_admin(payload.role)):
            count = db.query(User).filter(
                User.role.in_([UserRole.SUPER_ADMIN.value, UserRole.ADMIN.value]),
                User.is_active == True,
            ).count()
            if count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot deactivate or demote the only active Super Admin account",
                )

    resulting_role = target_user.role
    if payload.role is not None:
        normalized_role = UserRole.parse_assignment(payload.role)
        if normalized_role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role '{payload.role}'",
            )
        if UserRole.is_super_admin(normalized_role) and not target_user.is_super_admin:
            ensure_single_active_super_admin(db, target_user.id)
        resulting_role = normalized_role

    resulting_dept = target_user.department
    if payload.department is not None:
        if payload.department.strip() == "" or payload.department.upper() == "NONE":
            resulting_dept = None
        else:
            resulting_dept = Department.normalize(payload.department)

    # Mandatory department enforcement:
    # If the user has (or is being assigned) a non-Super Admin role, a valid department is strictly required.
    if not UserRole.is_super_admin(resulting_role):
        if not resulting_dept:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid department is required for Department Admin, Department Checker, and Department Maker roles. It cannot be unassigned.",
            )

    target_user.role = resulting_role
    target_user.department = resulting_dept

    if payload.password and payload.password.strip():
        target_user.password_hash = hash_password(payload.password.strip())

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

    action_name = "USER_ROLE_CHANGED" if payload.role is not None else "USER_UPDATE"
    log_activity(
        action=action_name,
        status="SUCCESS",
        actor_id=current_user.id,
        username=current_user.email,
        department=current_user.department,
        role=current_user.role,
        target_resource=f"user:{target_user.id}",
        details={
            "target_email": target_user.email,
            "role": target_user.role,
            "department": target_user.department,
            "is_active": target_user.is_active,
        },
    )
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
    summary="Admin only: Deactivate or permanently delete a member",
)
async def delete_user(
    user_id: int,
    permanent: bool = Query(False, description="If true, permanently delete user record"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Remove or deactivate a user safely.
    Handles foreign keys safely: disassociates documents and reviews without deleting documents.
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

    # Department Admin restriction
    if not current_user.is_super_admin:
        admin_dept = (current_user.department or "").strip().upper()
        target_dept = (target_user.department or "").strip().upper()
        if target_dept != admin_dept or target_user.is_super_admin:
            log_activity(
                action="UNAUTHORIZED_ACCESS",
                status="FAILURE",
                actor_id=current_user.id,
                username=current_user.email,
                department=current_user.department,
                role=current_user.role,
                target_resource=f"user:{user_id}",
                failure_reason="Department Admin cannot delete users outside their department",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Department Admins can only delete users within their assigned department",
            )

    if target_user.is_super_admin:
        count = db.query(User).filter(
            User.role.in_([UserRole.SUPER_ADMIN.value, UserRole.ADMIN.value]),
            User.is_active == True,
        ).count()
        if count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the only active Super Admin account",
            )

    target_name = target_user.name
    target_email = target_user.email

    if permanent:
        # Safe foreign key dissociation to preserve documents and attribution
        db.query(Document).filter(Document.uploaded_by == user_id).update({Document.uploaded_by: None})
        db.query(Document).filter(Document.assigned_checker == user_id).update({Document.assigned_checker: None})
        db.query(ReviewQueue).filter(ReviewQueue.assigned_to == user_id).update({ReviewQueue.assigned_to: None})
        db.query(ReviewQueue).filter(ReviewQueue.reviewed_by == user_id).update({ReviewQueue.reviewed_by: None})

        db.delete(target_user)
        db.commit()

        log_activity(
            action="USER_DELETE",
            status="SUCCESS",
            actor_id=current_user.id,
            username=current_user.email,
            department=current_user.department,
            role=current_user.role,
            target_resource=f"user:{user_id}",
            details={"email": target_email, "permanent": True},
        )
        return MessageResponse(message=f"Member '{target_name}' has been permanently deleted successfully")

    # Safe deactivation
    target_user.is_active = False
    db.commit()

    log_activity(
        action="USER_DEACTIVATE",
        status="SUCCESS",
        actor_id=current_user.id,
        username=current_user.email,
        department=current_user.department,
        role=current_user.role,
        target_resource=f"user:{user_id}",
        details={"email": target_email, "permanent": False},
    )
    return MessageResponse(message=f"Member '{target_name}' has been deactivated successfully")


# ============================================================
# ACTIVITY LOGS (AUDIT TRAIL)
# ============================================================

@router.get(
    "/activity-logs",
    response_model=ActivityLogListResponse,
    summary="Admin only: View audit logs with department isolation",
)
async def get_activity_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    department: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Search and filter backend-persisted activity logs.
    - Super Admin: sees system-wide events or filters by department
    - Department Admin: sees events for their department only
    """
    query = db.query(ActivityLog)

    if current_user.is_super_admin:
        if department and department.strip() and department.upper() != "ALL":
            dept_norm = Department.normalize(department)
            query = query.filter(func.upper(ActivityLog.department) == dept_norm)
    else:
        # Department Admin is strictly locked to their department
        admin_dept = (current_user.department or "").strip().upper()
        if department and department.strip() and Department.normalize(department) != admin_dept:
            log_activity(
                action="UNAUTHORIZED_ACCESS",
                status="FAILURE",
                actor_id=current_user.id,
                username=current_user.email,
                department=current_user.department,
                role=current_user.role,
                target_resource="activity_logs",
                failure_reason="Department Admin attempted to view other department activity logs",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Department Admins can only view activity logs for their assigned department",
            )
        query = query.filter(func.upper(ActivityLog.department) == admin_dept)

    if username and username.strip():
        query = query.filter(ActivityLog.username.ilike(f"%{username.strip()}%"))

    if action and action.strip() and action.upper() != "ALL":
        query = query.filter(ActivityLog.action == action.strip().upper())

    if status_filter and status_filter.strip() and status_filter.upper() != "ALL":
        query = query.filter(ActivityLog.status == status_filter.strip().upper())

    if start_date:
        query = query.filter(ActivityLog.timestamp >= start_date)

    if end_date:
        query = query.filter(ActivityLog.timestamp <= end_date)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                ActivityLog.action.ilike(term),
                ActivityLog.username.ilike(term),
                ActivityLog.target_resource.ilike(term),
                ActivityLog.failure_reason.ilike(term),
                ActivityLog.details.ilike(term),
            )
        )

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))
    items = query.order_by(ActivityLog.timestamp.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return ActivityLogListResponse(
        items=[ActivityLogResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


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
    log_activity(
        action="PERMISSION_UPDATE",
        status="SUCCESS",
        actor_id=current_user.id,
        username=current_user.email,
        department=current_user.department,
        role=current_user.role,
        target_resource="folder_permissions",
    )
    return updated


# ============================================================
# WORKFLOW RULES
# ============================================================

def _workflow_scope_department(current_user: User, requested_department: Optional[str]) -> Optional[str]:
    try:
        return resolve_workflow_scope_department(current_user, requested_department)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionError as exc:
        log_activity(
            action="UNAUTHORIZED_ACCESS",
            status="FAILURE",
            actor_id=current_user.id,
            username=current_user.email,
            department=current_user.department,
            role=current_user.role,
            target_resource="workflow_config",
            failure_reason=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _validate_workflow_rule_payload(
    db: Session,
    current_user: User,
    *,
    document_type: Optional[str],
    claimed_department: Optional[str],
    route: str,
    rule_type: str,
    condition: Optional[str],
    existing_rule: Any = None,
) -> str:
    if rule_type not in WORKFLOW_RULE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid rule type",
        )
    if condition and condition != WORKFLOW_CONDITION_CONFIDENCE_BELOW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported workflow condition",
        )

    canonical_type = canonical_document_type(document_type)
    if not canonical_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid document type is required",
        )

    rule_department = department_for_document_type(canonical_type)
    claimed = None
    if claimed_department and str(claimed_department).strip():
        claimed = normalize_known_department(claimed_department)
        if claimed is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown department",
            )
    if claimed and claimed != rule_department:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document type does not belong to the selected department",
        )

    if not can_manage_workflow_document_type(current_user, canonical_type):
        log_activity(
            action="UNAUTHORIZED_ACCESS",
            status="FAILURE",
            actor_id=current_user.id,
            username=current_user.email,
            department=current_user.department,
            role=current_user.role,
            target_resource="workflow_config",
            failure_reason="Attempted to manage a workflow rule outside permitted department",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Department Admins can only manage workflow rules for their assigned department",
        )

    if existing_rule is not None and not current_user.is_super_admin:
        if not rule_belongs_to_department(existing_rule, current_user.department):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot modify workflow rules belonging to other departments",
            )

    if not is_eligible_route(db, rule_department, route):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Route must be an eligible team or user for the selected department",
        )

    return canonical_type


@router.get(
    "/workflow",
    response_model=WorkflowConfigResponse,
    summary="Admin only: Get workflow confidence threshold and routing rules",
)
async def get_workflow(
    department: Optional[str] = Query(None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Retrieve the auto-flag threshold and active routing rules for the caller's permitted scope."""
    scope = _workflow_scope_department(current_user, department)
    config = get_workflow_configuration(db, department=scope)
    config["can_edit_threshold"] = bool(current_user.is_super_admin)
    return config


@router.get(
    "/workflow/options",
    response_model=WorkflowOptionsResponse,
    summary="Admin only: Dropdown catalogs for workflow routing",
)
async def workflow_options(
    department: Optional[str] = Query(None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    scope = _workflow_scope_department(current_user, department)
    departments = managed_workflow_departments(current_user)
    return get_workflow_options(
        db,
        departments=departments,
        selected_department=scope,
        can_edit_threshold=bool(current_user.is_super_admin),
    )


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
    from app.models.workflow import WorkflowRule

    can_edit_threshold = bool(current_user.is_super_admin)
    if not can_edit_threshold:
        current = get_workflow_configuration(db)
        if abs(float(payload.threshold) - float(current["threshold"])) > 0.001:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Super Admin can change the global auto-flag threshold",
            )

    rules_dict = None
    if payload.rules is not None:
        rules_dict = []
        for item in payload.rules:
            existing = None
            if item.id is not None:
                existing = db.query(WorkflowRule).filter(WorkflowRule.id == item.id).first()
                if existing is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Workflow rule with id {item.id} not found.",
                    )
            canonical_type = _validate_workflow_rule_payload(
                db,
                current_user,
                document_type=item.document_type,
                claimed_department=item.department,
                route=item.route,
                rule_type=item.type,
                condition=item.condition,
                existing_rule=existing,
            )
            dumped = item.model_dump()
            dumped["document_type"] = canonical_type
            dumped["department"] = department_for_document_type(canonical_type)
            rules_dict.append(dumped)

    scope = None if current_user.is_super_admin else _workflow_scope_department(current_user, None)
    updated = update_workflow_configuration(
        db,
        payload.threshold,
        rules_dict,
        update_threshold=can_edit_threshold,
        department=scope,
    )
    updated["can_edit_threshold"] = can_edit_threshold
    log_activity(
        action="WORKFLOW_UPDATE",
        status="SUCCESS",
        actor_id=current_user.id,
        username=current_user.email,
        department=current_user.department,
        role=current_user.role,
        target_resource="workflow_config",
        details={"threshold": payload.threshold},
    )
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
    canonical_type = _validate_workflow_rule_payload(
        db,
        current_user,
        document_type=payload.document_type,
        claimed_department=payload.department,
        route=payload.route,
        rule_type=payload.type,
        condition=payload.condition,
    )
    created = add_workflow_rule(
        db,
        title=payload.title,
        route=payload.route,
        rule_type=payload.type,
        threshold=payload.threshold,
        document_type=canonical_type,
    )
    log_activity(
        action="WORKFLOW_RULE_ADD",
        status="SUCCESS",
        actor_id=current_user.id,
        username=current_user.email,
        department=current_user.department,
        role=current_user.role,
        target_resource=f"workflow_rule:{created['id']}",
        details={"title": payload.title, "route": payload.route, "document_type": canonical_type},
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
    from app.models.workflow import WorkflowRule
    rule = db.query(WorkflowRule).filter(WorkflowRule.id == rule_id).first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow rule with id {rule_id} not found.",
        )
    if not current_user.is_super_admin:
        if not rule_belongs_to_department(rule, current_user.department):
            log_activity(
                action="UNAUTHORIZED_ACCESS",
                status="FAILURE",
                actor_id=current_user.id,
                username=current_user.email,
                department=current_user.department,
                role=current_user.role,
                target_resource=f"workflow_rule:{rule_id}",
                failure_reason="Attempted to delete a workflow rule outside permitted department",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot delete workflow rules belonging to other departments",
            )
    success = delete_workflow_rule(db, rule_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow rule with ID {rule_id} not found",
        )
    log_activity(
        action="WORKFLOW_RULE_DELETE",
        status="SUCCESS",
        actor_id=current_user.id,
        username=current_user.email,
        department=current_user.department,
        role=current_user.role,
        target_resource=f"workflow_rule:{rule_id}",
    )
    return MessageResponse(message="Routing rule removed successfully")

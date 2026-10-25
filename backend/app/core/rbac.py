from enum import Enum
from typing import Any


class UserRole(str, Enum):
    # Four primary roles
    SUPER_ADMIN = "SUPER_ADMIN"
    DEPARTMENT_ADMIN = "DEPARTMENT_ADMIN"
    DEPARTMENT_CHECKER = "DEPARTMENT_CHECKER"
    DEPARTMENT_MAKER = "DEPARTMENT_MAKER"

    # Backward compatibility aliases
    ADMIN = "ADMIN"
    UPLOAD_CHECKER = "UPLOAD_CHECKER"
    UPLOAD_MAKER = "UPLOAD_MAKER"

    @classmethod
    def normalize(cls, role_str: str | None) -> str:
        """Normalize role string case-insensitively to standard internal string."""
        if not role_str:
            return cls.DEPARTMENT_MAKER.value
        clean = role_str.strip().upper().replace(" ", "_")
        if clean in ("SUPER_ADMIN", "SUPERADMIN", "SUPER_ADMINISTRATOR"):
            return cls.SUPER_ADMIN.value
        if clean in ("ADMIN", "ADMINISTRATOR"):
            return cls.SUPER_ADMIN.value
        if clean in ("DEPARTMENT_ADMIN", "DEPT_ADMIN", "DEPARTMENTADMIN", "DEPTADMIN"):
            return cls.DEPARTMENT_ADMIN.value
        if clean in ("DEPARTMENT_CHECKER", "DEPT_CHECKER", "UPLOAD_CHECKER", "CHECKER"):
            return cls.DEPARTMENT_CHECKER.value
        if clean in ("DEPARTMENT_MAKER", "DEPT_MAKER", "UPLOAD_MAKER", "MAKER", "USER", "EDITOR", "VIEWER"):
            return cls.DEPARTMENT_MAKER.value
        return cls.DEPARTMENT_MAKER.value

    @classmethod
    def is_super_admin(cls, role_str: str | None) -> bool:
        if not role_str:
            return False
        clean = role_str.strip().upper().replace(" ", "_")
        return clean in ("SUPER_ADMIN", "SUPERADMIN", "SUPER_ADMINISTRATOR", "ADMIN", "ADMINISTRATOR")

    @classmethod
    def is_dept_admin(cls, role_str: str | None) -> bool:
        if not role_str:
            return False
        clean = role_str.strip().upper().replace(" ", "_")
        return clean in ("DEPARTMENT_ADMIN", "DEPT_ADMIN", "DEPARTMENTADMIN", "DEPTADMIN")

    @classmethod
    def is_admin(cls, role_str: str | None) -> bool:
        return cls.is_super_admin(role_str) or cls.is_dept_admin(role_str)

    @classmethod
    def is_checker(cls, role_str: str | None) -> bool:
        if not role_str:
            return False
        clean = role_str.strip().upper().replace(" ", "_")
        return clean in (
            "DEPARTMENT_CHECKER", "DEPT_CHECKER", "UPLOAD_CHECKER", "CHECKER",
            "SUPER_ADMIN", "ADMIN", "DEPARTMENT_ADMIN", "DEPT_ADMIN"
        )

    @classmethod
    def is_maker(cls, role_str: str | None) -> bool:
        if not role_str:
            return False
        clean = role_str.strip().upper().replace(" ", "_")
        return clean in (
            "DEPARTMENT_MAKER", "DEPT_MAKER", "UPLOAD_MAKER", "MAKER", "USER",
            "SUPER_ADMIN", "ADMIN", "DEPARTMENT_ADMIN", "DEPT_ADMIN"
        )

    @classmethod
    def is_strict_maker(cls, role_str: str | None) -> bool:
        if not role_str:
            return True
        clean = role_str.strip().upper().replace(" ", "_")
        return clean in ("DEPARTMENT_MAKER", "DEPT_MAKER", "UPLOAD_MAKER", "MAKER", "USER", "EDITOR", "VIEWER")

    @classmethod
    def parse_assignment(cls, role_str: str | None) -> str | None:
        """Accept supported role values for assignments, preserving legacy tests while supporting 4 roles."""
        if not role_str:
            return None
        clean = role_str.strip().upper().replace(" ", "_")
        if clean in ("SUPER_ADMIN", "SUPERADMIN", "SUPER_ADMINISTRATOR"):
            return cls.SUPER_ADMIN.value
        if clean in ("ADMIN", "ADMINISTRATOR"):
            return cls.ADMIN.value
        if clean in ("DEPARTMENT_ADMIN", "DEPT_ADMIN"):
            return cls.DEPARTMENT_ADMIN.value
        if clean in ("DEPARTMENT_CHECKER", "DEPT_CHECKER"):
            return cls.DEPARTMENT_CHECKER.value
        if clean in ("UPLOAD_CHECKER", "CHECKER"):
            return cls.UPLOAD_CHECKER.value
        if clean in ("DEPARTMENT_MAKER", "DEPT_MAKER"):
            return cls.DEPARTMENT_MAKER.value
        if clean in ("UPLOAD_MAKER", "MAKER", "USER"):
            return cls.UPLOAD_MAKER.value
        return None

    @classmethod
    def to_display_label(cls, role_str: str | None) -> str:
        if cls.is_super_admin(role_str):
            return "Super Admin"
        if cls.is_dept_admin(role_str):
            return "Department Admin"
        clean = (role_str or "").strip().upper().replace(" ", "_")
        if clean in ("DEPARTMENT_CHECKER", "DEPT_CHECKER", "UPLOAD_CHECKER", "CHECKER"):
            return "Department Checker"
        return "Department Maker"


# Legacy alias
Role = UserRole


class Department(str, Enum):
    HR = "HR"
    FINANCE = "FINANCE"
    LEGAL = "LEGAL"
    OPERATIONS = "OPERATIONS"
    IT = "IT"

    @classmethod
    def normalize(cls, dept_str: str | None) -> str | None:
        """Normalize department string to standard Department enum value."""
        if not dept_str:
            return None
        upper = dept_str.strip().upper()
        for dept in cls:
            if dept.value.upper() == upper:
                return dept.value
        return upper


class DocumentStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_REVISION = "NEEDS_REVISION"
    COMPLETED = "COMPLETED"

    @classmethod
    def normalize(cls, status_str: str | None) -> str:
        """Normalize status string to standard DocumentStatus enum value."""
        if not status_str:
            return cls.UPLOADED.value
        upper = status_str.strip().upper()
        if upper in ("COMPLETED", "PROCESSED", "INDEXED"):
            return cls.APPROVED.value
        if upper in ("NEEDS_REVIEW", "PENDING"):
            return cls.PENDING_REVIEW.value
        if upper in ("FAILED", "REJECTED"):
            return cls.REJECTED.value
        for status in cls:
            if status.value == upper:
                return status.value
        return upper


# Department to document type mappings
DEPARTMENT_DOCUMENT_TYPES: dict[Department, list[str]] = {
    Department.HR: [
        "Employee Records",
        "Policies",
        "Appraisals",
    ],
    Department.FINANCE: [
        "Invoices",
        "Budget Reports",
        "Tax Documents",
    ],
    Department.LEGAL: [
        "Contracts",
        "Agreements",
        "Compliance Documents",
    ],
    Department.OPERATIONS: [
        "SOPs",
        "Operational Reports",
    ],
    Department.IT: [
        "Technical Documentation",
        "Change Requests",
    ],
}

DOCUMENT_TYPE_TO_DEPARTMENT: dict[str, Department] = {
    doc_type: dept
    for dept, doc_types in DEPARTMENT_DOCUMENT_TYPES.items()
    for doc_type in doc_types
}

# Workflow routing catalogs used by Settings. Values match existing rule schema
# and department document-type mappings rather than UI-only lists.
WORKFLOW_RULE_TYPES: tuple[str, ...] = ("warning", "security", "claims")
WORKFLOW_CONDITION_CONFIDENCE_BELOW = "confidence_below_threshold"
WORKFLOW_CONDITIONS: tuple[str, ...] = (WORKFLOW_CONDITION_CONFIDENCE_BELOW,)


def known_departments() -> list[str]:
    return [dept.value for dept in Department]


def normalize_known_department(dept_str: str | None) -> str | None:
    normalized = Department.normalize(dept_str)
    if not normalized:
        return None
    allowed = {dept.value for dept in Department}
    return normalized if normalized in allowed else None


def document_types_for_department(department: str | None) -> list[str]:
    dept_value = normalize_known_department(department)
    if not dept_value:
        return []
    dept_enum = next((dept for dept in Department if dept.value == dept_value), None)
    if dept_enum is None:
        return []
    return list(DEPARTMENT_DOCUMENT_TYPES.get(dept_enum, []))


def department_for_document_type(document_type: str | None) -> str | None:
    if not document_type or not str(document_type).strip():
        return None
    key = str(document_type).strip()
    mapped = DOCUMENT_TYPE_TO_DEPARTMENT.get(key)
    if mapped is not None:
        return mapped.value
    for doc_type, dept in DOCUMENT_TYPE_TO_DEPARTMENT.items():
        if doc_type.lower() == key.lower():
            return dept.value
    return normalize_known_department(key)


def canonical_document_type(document_type: str | None) -> str | None:
    if not document_type or not str(document_type).strip():
        return None
    key = str(document_type).strip()
    for doc_type in DOCUMENT_TYPE_TO_DEPARTMENT:
        if doc_type.lower() == key.lower():
            return doc_type
    return None


def managed_workflow_departments(user: Any) -> list[str]:
    """Departments the authenticated user may manage workflow routing for."""
    if not user:
        return []
    role = getattr(user, "role", None)
    if UserRole.is_super_admin(role):
        return known_departments()
    if UserRole.is_dept_admin(role):
        dept = normalize_known_department(getattr(user, "department", None))
        return [dept] if dept else []
    return []


def can_manage_workflow_department(user: Any, department: str | None) -> bool:
    dept = normalize_known_department(department)
    if not dept:
        return False
    return dept in managed_workflow_departments(user)


def can_manage_workflow_document_type(user: Any, document_type: str | None) -> bool:
    canonical = canonical_document_type(document_type)
    if not canonical:
        return False
    return can_manage_workflow_department(user, department_for_document_type(canonical))


def resolve_workflow_scope_department(user: Any, requested_department: str | None) -> str | None:
    """
    Resolve workflow department scope from the authenticated user.

    Client-supplied department is never trusted:
    - Super Admin may request a known department, or omit it to see all.
    - Department Admin is always scoped to their assigned department.
    """
    requested = normalize_known_department(requested_department) if requested_department else None
    if requested_department and str(requested_department).strip() and requested is None:
        raise ValueError("Unknown department")

    role = getattr(user, "role", None)
    if UserRole.is_super_admin(role):
        return requested

    if UserRole.is_dept_admin(role):
        own = normalize_known_department(getattr(user, "department", None))
        if not own:
            raise PermissionError("Department Admin has no department assigned")
        if requested and requested != own:
            raise PermissionError("Department Admins can only access workflow routing for their assigned department")
        return own

    raise PermissionError("Workflow routing is limited to Super Admin and Department Admin")


def can_view_document(user: Any, document: Any) -> bool:
    """
    Check if a user has permission to view a document.
    - Super Admin: all documents across all departments
    - Department Admin / Checker / Maker: strictly restricted to their assigned department
    - Cross-department access is strictly forbidden (e.g. Finance users must NEVER access HR documents).
    """
    if not user or not document:
        return False

    user_role = getattr(user, "role", None)
    if UserRole.is_super_admin(user_role):
        return True

    user_dept = getattr(user, "department", None)
    if not user_dept or not str(user_dept).strip():
        # Non-Super Admin with no department cannot view department documents
        return False

    doc_dept = getattr(document, "department", None)
    if not doc_dept or not str(doc_dept).strip():
        # Unassigned document without department: only Super Admin can view
        return False

    # Strict department matching
    return str(user_dept).strip().upper() == str(doc_dept).strip().upper()


def can_review_document(user: Any, document: Any) -> tuple[bool, str | None]:
    """
    Check if a user is permitted to review/approve/reject a document.
    Enforces separation of duties and department isolation:
    - User must NOT be a Department Maker
    - User must NOT be the document uploader (Separation of Duties!)
    - Super Admin: all authorized departments
    - Department Admin: own department
    - Department Checker: own department only
    """
    if not user:
        return False, "Authentication required"

    user_role = getattr(user, "role", None)
    if UserRole.is_strict_maker(user_role):
        return False, "Department Makers cannot access the Review Queue or verify documents"

    if not (UserRole.is_super_admin(user_role) or UserRole.is_dept_admin(user_role) or UserRole.is_checker(user_role)):
        return False, "Review authority required"

    user_id = getattr(user, "id", None)
    uploader_id = getattr(document, "uploaded_by", None)

    # CRITICAL: Separation of Duties
    if user_id is not None and uploader_id is not None and user_id == uploader_id:
        return False, "Separation of duties violation: You cannot approve or reject a document you uploaded."

    # Super Admin has review authority across all departments (provided separation of duties is met)
    if UserRole.is_super_admin(user_role):
        return True, None

    # Department isolation for Department Admin and Department Checker
    user_dept = getattr(user, "department", None)
    doc_dept = getattr(document, "department", None)

    if not user_dept or not str(user_dept).strip():
        return False, "User has no department assigned"

    if not doc_dept or not str(doc_dept).strip():
        return False, "Document has no department assigned"

    if str(user_dept).strip().upper() != str(doc_dept).strip().upper():
        return False, "Document is outside your permitted department"

    # For checkers: if specifically assigned to another checker, enforce assignment
    if not UserRole.is_dept_admin(user_role):
        assigned_checker = getattr(document, "assigned_checker", None)
        if assigned_checker is not None and assigned_checker != user_id:
            return False, "Document is assigned to another checker"

    return True, None

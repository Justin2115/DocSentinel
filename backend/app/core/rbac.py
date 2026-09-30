from enum import Enum
from typing import Any


class UserRole(str, Enum):
    ADMIN = "ADMIN"
    UPLOAD_MAKER = "UPLOAD_MAKER"
    UPLOAD_CHECKER = "UPLOAD_CHECKER"

    @classmethod
    def normalize(cls, role_str: str | None) -> str:
        """Normalize role string case-insensitively, handling legacy 'admin' and 'user'."""
        if not role_str:
            return cls.UPLOAD_MAKER.value
        clean = role_str.strip().upper().replace(" ", "_")
        if clean in ("ADMIN", "ADMINISTRATOR"):
            return cls.ADMIN.value
        if clean in ("UPLOAD_CHECKER", "CHECKER"):
            return cls.UPLOAD_CHECKER.value
        if clean in ("UPLOAD_MAKER", "MAKER", "USER", "EDITOR", "VIEWER"):
            return cls.UPLOAD_MAKER.value
        return cls.UPLOAD_MAKER.value

    @classmethod
    def parse_assignment(cls, role_str: str | None) -> str | None:
        """Accept only the three supported role values for new assignments."""
        if not role_str:
            return None
        clean = role_str.strip().upper().replace(" ", "_")
        return clean if clean in {role.value for role in cls} else None

    @classmethod
    def to_display_label(cls, role_str: str | None) -> str:
        norm = cls.normalize(role_str)
        if norm == cls.ADMIN.value:
            return "Admin"
        if norm == cls.UPLOAD_CHECKER.value:
            return "Upload Checker"
        return "Upload Maker"


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
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_REVISION = "NEEDS_REVISION"

    @classmethod
    def normalize(cls, status_str: str | None) -> str:
        """Normalize status string to standard DocumentStatus enum value."""
        if not status_str:
            return cls.UPLOADED.value
        upper = status_str.strip().upper()
        # Handle legacy status values
        if upper in ("COMPLETED", "PROCESSED", "INDEXED"):
            return cls.APPROVED.value
        if upper in ("NEEDS_REVIEW", "PENDING", "PROCESSING"):
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

# Reverse mapping: Document Type -> Department
DOCUMENT_TYPE_TO_DEPARTMENT: dict[str, Department] = {
    doc_type: dept
    for dept, doc_types in DEPARTMENT_DOCUMENT_TYPES.items()
    for doc_type in doc_types
}


def can_view_document(user: Any, document: Any) -> bool:
    """
    Check if a user has permission to view a document.
    - ADMIN: all documents across all departments
    - UPLOAD_MAKER: documents in their assigned department or documents uploaded by themselves
    - UPLOAD_CHECKER: documents assigned to them or within their assigned department
    """
    if not user:
        return False

    user_role = UserRole.normalize(getattr(user, "role", None))
    if user_role == UserRole.ADMIN.value:
        return True

    user_id = getattr(user, "id", None)
    uploader_id = getattr(document, "uploaded_by", None)
    user_dept = getattr(user, "department", None)
    doc_dept = getattr(document, "department", None)

    # User's own document
    if user_id is not None and uploader_id is not None and user_id == uploader_id:
        return True

    # Upload Maker: department match or unassigned document
    if user_role == UserRole.UPLOAD_MAKER.value:
        if user_dept and doc_dept:
            return user_dept.strip().upper() == doc_dept.strip().upper()
        return not doc_dept

    # Upload Checker: documents assigned to them and within their permitted department
    if user_role == UserRole.UPLOAD_CHECKER.value:
        assigned_checker = getattr(document, "assigned_checker", None)
        if assigned_checker == user_id:
            if user_dept and doc_dept and user_dept.strip().upper() != doc_dept.strip().upper():
                return False
            return True
        return False

    return False



def can_review_document(user: Any, document: Any) -> tuple[bool, str | None]:
    """
    Check if a user is permitted to review/approve/reject a document.
    Enforces separation of duties:
    - User must be ADMIN or UPLOAD_CHECKER
    - Must NOT be the document uploader (Separation of Duties!)
    - If UPLOAD_CHECKER, must be assigned or within permitted department
    """
    if not user:
        return False, "Authentication required"

    user_role = UserRole.normalize(getattr(user, "role", None))
    if user_role not in (UserRole.ADMIN.value, UserRole.UPLOAD_CHECKER.value):
        return False, "Only Upload Checkers and Admins can review documents"

    user_id = getattr(user, "id", None)
    uploader_id = getattr(document, "uploaded_by", None)

    # CRITICAL: Separation of Duties
    if user_id is not None and uploader_id is not None and user_id == uploader_id:
        return False, "Separation of duties violation: You cannot approve or reject a document you uploaded."

    # Admin has review authority across all documents (provided separation of duties is met)
    if user_role == UserRole.ADMIN.value:
        return True, None

    # For UPLOAD_CHECKER: Approval/rejection/revision requires document to be assigned to that checker
    assigned_checker = getattr(document, "assigned_checker", None)
    if assigned_checker is None or assigned_checker != user_id:
        return False, "Review authority requires the document to be assigned to you"

    # Also verify department access if departments are configured
    user_dept = getattr(user, "department", None)
    doc_dept = getattr(document, "department", None)
    if user_dept and doc_dept and user_dept.strip().upper() != doc_dept.strip().upper():
        return False, "Document is outside your permitted department"

    return True, None


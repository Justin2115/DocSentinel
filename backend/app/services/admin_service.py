from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.rbac import UserRole
from app.models.permission import FolderPermission
from app.models.user import User
from app.models.workflow import WorkflowRule, WorkflowSetting

logger = logging.getLogger(__name__)

DEFAULT_FOLDERS = [
    {
        "folder": "Invoices",
        "admin": True,
        "upload_maker": True,
        "upload_checker": True,
    },
    {
        "folder": "ID Verification",
        "admin": True,
        "upload_maker": False,
        "upload_checker": False,
    },
    {
        "folder": "Contracts",
        "admin": True,
        "upload_maker": True,
        "upload_checker": True,
    },
    {
        "folder": "Medical Claims",
        "admin": True,
        "upload_maker": False,
        "upload_checker": True,
    },
]

DEFAULT_ROUTING_RULES = [
    {
        "rule_type": "warning",
        "title": "Invoices below 80% confidence",
        "route": "Finance team",
        "document_type": "Invoices",
        "threshold": 80.0,
    },
    {
        "rule_type": "security",
        "title": "ID documents below 70% confidence",
        "route": "Compliance team",
        "document_type": "ID Document",
        "threshold": 70.0,
    },
    {
        "rule_type": "claims",
        "title": "Medical claims below 85% confidence",
        "route": "Claims review team",
        "document_type": "Medical Claim",
        "threshold": 85.0,
    },
]


def init_system_settings_and_permissions(db: Session) -> None:
    """Initialize default permissions and workflow rules in database and normalize user roles."""
    # 1. Normalize existing user roles to ensure consistency
    try:
        users = db.query(User).all()
        for u in users:
            normalized = UserRole.normalize(u.role)
            if u.role != normalized:
                u.role = normalized
        db.commit()
    except Exception as e:
        logger.warning("Could not normalize user roles: %s", e)
        db.rollback()

    # 2. Seed folder permissions if not present
    try:
        existing_perm_count = db.query(FolderPermission).count()
        if existing_perm_count == 0:
            for item in DEFAULT_FOLDERS:
                folder = item["folder"]
                db.add(
                    FolderPermission(
                        folder_name=folder,
                        role=UserRole.ADMIN.value,
                        can_view=item["admin"],
                        can_upload=item["admin"],
                        can_review=item["admin"],
                    )
                )
                db.add(
                    FolderPermission(
                        folder_name=folder,
                        role=UserRole.UPLOAD_MAKER.value,
                        can_view=item["upload_maker"],
                        can_upload=item["upload_maker"],
                        can_review=False,
                    )
                )
                db.add(
                    FolderPermission(
                        folder_name=folder,
                        role=UserRole.UPLOAD_CHECKER.value,
                        can_view=item["upload_checker"],
                        can_upload=False,
                        can_review=item["upload_checker"],
                    )
                )
            db.commit()
            logger.info("Default folder permissions seeded successfully.")
    except Exception as e:
        logger.warning("Could not seed default folder permissions: %s", e)
        db.rollback()

    # 3. Seed workflow setting if not present
    try:
        setting = db.query(WorkflowSetting).first()
        if not setting:
            db.add(WorkflowSetting(confidence_threshold=80.0))
            db.commit()
            logger.info("Default workflow settings seeded with threshold 80.0%.")
    except Exception as e:
        logger.warning("Could not seed default workflow settings: %s", e)
        db.rollback()

    # 4. Seed workflow rules if not present
    try:
        rule_count = db.query(WorkflowRule).count()
        if rule_count == 0:
            for r in DEFAULT_ROUTING_RULES:
                db.add(
                    WorkflowRule(
                        rule_type=r["rule_type"],
                        title=r["title"],
                        route=r["route"],
                        document_type=r.get("document_type"),
                        threshold=r["threshold"],
                        is_active=True,
                    )
                )
            db.commit()
            logger.info("Default workflow routing rules seeded successfully.")
    except Exception as e:
        logger.warning("Could not seed default workflow routing rules: %s", e)
        db.rollback()


def get_folder_permissions_matrix(db: Session) -> list[dict[str, Any]]:
    """Return the permissions matrix aggregated per folder."""
    all_perms = db.query(FolderPermission).all()
    matrix_map: dict[str, dict[str, Any]] = {}

    for p in all_perms:
        folder = p.folder_name
        if folder not in matrix_map:
            matrix_map[folder] = {
                "folder": folder,
                "admin": True,
                "upload_maker": False,
                "upload_checker": False,
            }
        role_norm = UserRole.normalize(p.role)
        if role_norm == UserRole.UPLOAD_MAKER.value:
            matrix_map[folder]["upload_maker"] = p.can_view
        elif role_norm == UserRole.UPLOAD_CHECKER.value:
            matrix_map[folder]["upload_checker"] = p.can_view

    # Fallback to DEFAULT_FOLDERS if nothing in DB yet
    if not matrix_map:
        return [dict(f) for f in DEFAULT_FOLDERS]

    return list(matrix_map.values())


def update_folder_permissions_matrix(
    db: Session, permissions_list: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Persist updated folder permissions matrix to the database."""
    for item in permissions_list:
        folder = item.get("folder")
        if not folder:
            continue

        maker_allowed = bool(item.get("upload_maker", False))
        checker_allowed = bool(item.get("upload_checker", False))

        # Update or create Admin permission
        p_admin = (
            db.query(FolderPermission)
            .filter(
                FolderPermission.folder_name == folder,
                FolderPermission.role == UserRole.ADMIN.value,
            )
            .first()
        )
        if not p_admin:
            p_admin = FolderPermission(
                folder_name=folder, role=UserRole.ADMIN.value
            )
            db.add(p_admin)
        p_admin.can_view = True
        p_admin.can_upload = True
        p_admin.can_review = True

        # Update or create Upload Maker permission
        p_maker = (
            db.query(FolderPermission)
            .filter(
                FolderPermission.folder_name == folder,
                FolderPermission.role == UserRole.UPLOAD_MAKER.value,
            )
            .first()
        )
        if not p_maker:
            p_maker = FolderPermission(
                folder_name=folder, role=UserRole.UPLOAD_MAKER.value
            )
            db.add(p_maker)
        p_maker.can_view = maker_allowed
        p_maker.can_upload = maker_allowed
        p_maker.can_review = False

        # Update or create Upload Checker permission
        p_checker = (
            db.query(FolderPermission)
            .filter(
                FolderPermission.folder_name == folder,
                FolderPermission.role == UserRole.UPLOAD_CHECKER.value,
            )
            .first()
        )
        if not p_checker:
            p_checker = FolderPermission(
                folder_name=folder, role=UserRole.UPLOAD_CHECKER.value
            )
            db.add(p_checker)
        p_checker.can_view = checker_allowed
        p_checker.can_upload = False
        p_checker.can_review = checker_allowed

    db.commit()
    return get_folder_permissions_matrix(db)


def check_folder_permission(
    db: Session, folder_or_doc_type: str | None, role: str, action: str = "view"
) -> bool:
    """Verify if a role has permission for a specific folder/document type."""
    normalized_role = UserRole.normalize(role)
    if normalized_role == UserRole.ADMIN.value:
        return True

    if not folder_or_doc_type:
        return True

    perm = (
        db.query(FolderPermission)
        .filter(
            func.lower(FolderPermission.folder_name)
            == folder_or_doc_type.strip().lower(),
            FolderPermission.role == normalized_role,
        )
        .first()
    )

    if perm is None:
        # Default allow if folder not specifically restricted in matrix
        return True

    if action == "view":
        return perm.can_view
    if action == "upload":
        return perm.can_upload
    if action == "review":
        return perm.can_review
    return perm.can_view


def get_workflow_configuration(db: Session) -> dict[str, Any]:
    """Get active threshold and routing rules."""
    setting = db.query(WorkflowSetting).first()
    threshold = setting.confidence_threshold if setting else 80.0

    rules = (
        db.query(WorkflowRule)
        .filter(WorkflowRule.is_active == True)
        .order_by(WorkflowRule.id.asc())
        .all()
    )

    return {
        "threshold": threshold,
        "rules": [
            {
                "id": r.id,
                "type": r.rule_type,
                "title": r.title,
                "route": r.route,
                "document_type": r.document_type,
                "threshold": r.threshold,
            }
            for r in rules
        ],
    }


def update_workflow_configuration(
    db: Session, threshold: float, rules: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Update confidence threshold and optionally update/replace routing rules."""
    setting = db.query(WorkflowSetting).first()
    if not setting:
        setting = WorkflowSetting(confidence_threshold=threshold)
        db.add(setting)
    else:
        setting.confidence_threshold = threshold

    if rules is not None:
        # Keep existing rules or sync
        for rule_item in rules:
            rule_id = rule_item.get("id")
            if rule_id:
                r = (
                    db.query(WorkflowRule)
                    .filter(WorkflowRule.id == rule_id)
                    .first()
                )
                if r:
                    r.title = rule_item.get("title", r.title)
                    r.route = rule_item.get("route", r.route)
                    r.rule_type = rule_item.get("type", r.rule_type)
                    r.document_type = rule_item.get("document_type", r.document_type)
                    r.threshold = rule_item.get("threshold", r.threshold)
            else:
                # New rule
                new_rule = WorkflowRule(
                    rule_type=rule_item.get("type", "warning"),
                    title=rule_item.get("title", "New Rule"),
                    route=rule_item.get("route", "Review team"),
                    document_type=rule_item.get("document_type"),
                    threshold=float(rule_item.get("threshold", threshold)),
                    is_active=True,
                )
                db.add(new_rule)

    db.commit()
    return get_workflow_configuration(db)


def add_workflow_rule(
    db: Session, title: str, route: str, rule_type: str = "warning", threshold: float = 80.0, document_type: str | None = None
) -> dict[str, Any]:
    """Create a new routing rule."""
    rule = WorkflowRule(
        title=title.strip(),
        route=route.strip(),
        rule_type=rule_type.strip(),
        threshold=threshold,
        document_type=document_type.strip() if document_type else None,
        is_active=True,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {
        "id": rule.id,
        "type": rule.rule_type,
        "title": rule.title,
        "route": rule.route,
        "document_type": rule.document_type,
        "threshold": rule.threshold,
    }


def delete_workflow_rule(db: Session, rule_id: int) -> bool:
    """Delete or deactivate a routing rule."""
    rule = db.query(WorkflowRule).filter(WorkflowRule.id == rule_id).first()
    if not rule:
        return False
    db.delete(rule)
    db.commit()
    return True


def get_active_confidence_threshold(db: Session) -> float:
    """Retrieve current auto-flag confidence threshold from DB."""
    setting = db.query(WorkflowSetting).first()
    if setting and setting.confidence_threshold:
        return float(setting.confidence_threshold)
    return 80.0


def evaluate_document_routing(
    db: Session, document_type: str | None, confidence: float
) -> tuple[bool, str | None, str | None]:
    """
    Check if a document triggers auto-flagging and determine routing note.
    Returns: (requires_review, review_reason, route_to)
    """
    threshold = get_active_confidence_threshold(db)
    requires_review = confidence < threshold
    route_to = None
    reason = None

    if requires_review:
        reason = f"Confidence score ({confidence:.1f}%) is below auto-flag threshold ({threshold:.1f}%)"

    # Check routing rules
    if document_type:
        rule = (
            db.query(WorkflowRule)
            .filter(
                WorkflowRule.is_active == True,
                func.lower(WorkflowRule.document_type) == document_type.strip().lower(),
                WorkflowRule.threshold >= confidence,
            )
            .first()
        )
        if rule:
            route_to = rule.route
            requires_review = True
            reason = f"{rule.title}: Routed to {rule.route}"

    return requires_review, reason, route_to

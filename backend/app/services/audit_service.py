import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

import app.db.session as db_session
from app.models.audit import ActivityLog

logger = logging.getLogger(__name__)

# Sensitive keys that must NEVER be logged
SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "jwt_secret",
    "authorization",
    "credentials",
    "session",
}


def sanitize_details(data: Any) -> str | None:
    """Sanitize detail payloads to ensure no secrets or sensitive contents are stored."""
    if data is None:
        return None
    if isinstance(data, str):
        return data[:2000]

    if isinstance(data, dict):
        clean = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in SENSITIVE_KEYS):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                clean[k] = v
            else:
                clean[k] = str(v)[:200]
        try:
            return json.dumps(clean)[:2000]
        except Exception:
            return str(clean)[:2000]

    return str(data)[:2000]


def log_activity(
    action: str,
    status: str = "SUCCESS",
    actor_id: Optional[int] = None,
    username: Optional[str] = None,
    department: Optional[str] = None,
    role: Optional[str] = None,
    target_resource: Optional[str] = None,
    failure_reason: Optional[str] = None,
    correlation_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[Any] = None,
    db: Optional[Session] = None,
) -> None:
    """
    Safely record an activity log entry without ever raising unhandled exceptions
    that could break the main application flow.
    """
    try:
        sanitized_details_str = sanitize_details(details)
        clean_failure_reason = str(failure_reason)[:500] if failure_reason else None

        # Sanitize failure reason to ensure no passwords or tokens slip in
        if clean_failure_reason:
            for s in SENSITIVE_KEYS:
                if s in clean_failure_reason.lower():
                    clean_failure_reason = "Authentication or security error"
                    break

        entry = ActivityLog(
            actor_id=actor_id,
            username=username[:255] if username else None,
            department=department[:50] if department else None,
            role=role[:50] if role else None,
            action=action[:100],
            target_resource=target_resource[:255] if target_resource else None,
            status=status[:20],
            failure_reason=clean_failure_reason,
            correlation_id=correlation_id[:100] if correlation_id else None,
            ip_address=ip_address[:50] if ip_address else None,
            details=sanitized_details_str,
        )

        # Write using an isolated short-lived session so main transaction is untouched
        with db_session.SessionLocal() as session:
            session.add(entry)
            session.commit()

    except Exception as e:
        # Recoverable logging failure: do not crash the calling operation
        logger.warning(f"Failed to record activity log ({action}): {e}")

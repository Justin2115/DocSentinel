from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rbac import UserRole
from app.core.security import hash_password, verify_password
from app.models.user import User

logger = logging.getLogger(__name__)


def init_default_admin(db: Session) -> None:
    """Ensure the default super admin user exists on startup without overwriting on subsequent runs."""
    admin_email = settings.DEFAULT_ADMIN_EMAIL.strip().lower()
    existing_admin = db.query(User).filter(User.email == admin_email).first()

    if existing_admin:
        if not existing_admin.is_super_admin:
            existing_admin.role = UserRole.SUPER_ADMIN.value
            existing_admin.is_active = True
            db.commit()
            logger.info("Updated existing user %s to Super Admin role.", admin_email)
        else:
            logger.info("Default super admin user %s already exists.", admin_email)
        return

    logger.info("Creating default Super Admin account: %s", admin_email)
    new_admin = User(
        name="System Administrator",
        email=admin_email,
        password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
        role=UserRole.SUPER_ADMIN.value,
        is_active=True,
    )

    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    logger.info("Default Super Admin account created successfully with ID: %s", new_admin.id)


def authenticate_user_credentials(db: Session, email: str, password: str) -> tuple[User | None, str | None]:
    """
    Authenticate a user by email and password hash.
    Returns (user, failure_reason).
    """
    clean_email = email.strip().lower()
    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        return None, "Invalid email or password"

    if not user.is_active:
        return None, "Account is deactivated"

    if not user.password_hash or not user.password_hash.strip():
        return None, "Account has no password configured. Please sign in via Google OAuth or contact your administrator."

    if not verify_password(password, user.password_hash):
        return None, "Invalid email or password"

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user, None


def upsert_google_user(db: Session, google_info: dict[str, Any]) -> User:
    """Find or create a user from verified Google profile information."""
    google_id = google_info.get("sub")
    email = (google_info.get("email") or "").strip().lower()
    name = google_info.get("name") or email.split("@")[0]
    picture = google_info.get("picture")

    if not email:
        raise ValueError("Google account did not return a valid email address.")

    user = None
    if google_id:
        user = db.query(User).filter(User.google_id == google_id).first()

    if not user:
        user = db.query(User).filter(User.email == email).first()

    now = datetime.now(timezone.utc)

    if user:
        if not user.is_active:
            raise ValueError("Account is deactivated. Please contact administrator.")
        if google_id and not user.google_id:
            user.google_id = google_id
        if name and not user.is_admin:
            user.name = name
        if picture:
            user.profile_picture = picture
        user.last_login = now
        db.commit()
        db.refresh(user)
        logger.info("Existing user %s logged in via Google.", user.email)
        return user

    new_user = User(
        google_id=google_id,
        email=email,
        name=name,
        profile_picture=picture,
        role=UserRole.DEPARTMENT_MAKER.value,
        is_active=True,
        last_login=now,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    logger.info("Created new user %s (ID: %s) via Google OAuth.", new_user.email, new_user.id)
    return new_user

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.user import User

logger = logging.getLogger(__name__)


from app.core.rbac import UserRole

def init_default_admin(db: Session) -> None:
    """Ensure the default admin user exists on startup without overwriting on subsequent runs."""
    admin_email = settings.DEFAULT_ADMIN_EMAIL.strip().lower()
    existing_admin = db.query(User).filter(User.email == admin_email).first()

    if existing_admin:
        # If user exists, make sure they have admin role
        if not existing_admin.is_admin:
            existing_admin.role = UserRole.ADMIN.value
            db.commit()
            logger.info("Updated existing user %s to admin role.", admin_email)
        else:
            logger.info("Default admin user %s already exists.", admin_email)
        return

    logger.info("Creating default admin account: %s", admin_email)
    new_admin = User(
        name="System Administrator",
        email=admin_email,
        password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
        role=UserRole.ADMIN.value,
        is_active=True,
    )

    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    logger.info("Default admin account created successfully with ID: %s", new_admin.id)


def authenticate_user_credentials(db: Session, email: str, password: str) -> User | None:
    """Authenticate a user by email and password hash."""
    clean_email = email.strip().lower()
    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        return None

    if not user.password_hash or not verify_password(password, user.password_hash):
        return None

    if not user.is_active:
        return None

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def upsert_google_user(db: Session, google_info: dict[str, Any]) -> User:
    """Find or create a user from verified Google profile information."""
    google_id = google_info.get("sub")
    email = (google_info.get("email") or "").strip().lower()
    name = google_info.get("name") or email.split("@")[0]
    picture = google_info.get("picture")

    if not email:
        raise ValueError("Google account did not return a valid email address.")

    # 1. Search by google_id
    user = None
    if google_id:
        user = db.query(User).filter(User.google_id == google_id).first()

    # 2. Search by email if not found by google_id
    if not user:
        user = db.query(User).filter(User.email == email).first()

    now = datetime.now(timezone.utc)

    if user:
        # Update profile info and last login
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

    # 3. Create new user with default role 'UPLOAD_MAKER'
    new_user = User(
        google_id=google_id,
        email=email,
        name=name,
        profile_picture=picture,
        role=UserRole.UPLOAD_MAKER.value,
        is_active=True,
        last_login=now,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    logger.info("Created new user %s (ID: %s) via Google OAuth.", new_user.email, new_user.id)
    return new_user

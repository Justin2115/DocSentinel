from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('SUPER_ADMIN', 'DEPARTMENT_ADMIN', 'DEPARTMENT_CHECKER', 'DEPARTMENT_MAKER', 'ADMIN', 'UPLOAD_MAKER', 'UPLOAD_CHECKER')",
            name="ck_users_supported_role",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )
    profile_picture: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    google_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=True,
    )
    password_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    role: Mapped[str] = mapped_column(
        String(50),
        default="DEPARTMENT_MAKER",
        server_default="DEPARTMENT_MAKER",
        nullable=False,
    )
    department: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    @property
    def is_super_admin(self) -> bool:
        from app.core.rbac import UserRole
        norm = UserRole.normalize(self.role)
        return norm in (UserRole.SUPER_ADMIN.value, UserRole.ADMIN.value)

    @property
    def is_dept_admin(self) -> bool:
        from app.core.rbac import UserRole
        norm = UserRole.normalize(self.role)
        return norm == UserRole.DEPARTMENT_ADMIN.value

    @property
    def is_admin(self) -> bool:
        return self.is_super_admin or self.is_dept_admin

    @property
    def is_maker(self) -> bool:
        from app.core.rbac import UserRole
        norm = UserRole.normalize(self.role)
        return norm in (
            UserRole.DEPARTMENT_MAKER.value,
            UserRole.UPLOAD_MAKER.value,
            UserRole.SUPER_ADMIN.value,
            UserRole.ADMIN.value,
            UserRole.DEPARTMENT_ADMIN.value,
        )

    @property
    def is_checker(self) -> bool:
        from app.core.rbac import UserRole
        norm = UserRole.normalize(self.role)
        return norm in (
            UserRole.DEPARTMENT_CHECKER.value,
            UserRole.UPLOAD_CHECKER.value,
            UserRole.SUPER_ADMIN.value,
            UserRole.ADMIN.value,
            UserRole.DEPARTMENT_ADMIN.value,
        )

    @property
    def is_strict_maker(self) -> bool:
        from app.core.rbac import UserRole
        norm = UserRole.normalize(self.role)
        return norm in (UserRole.DEPARTMENT_MAKER.value, UserRole.UPLOAD_MAKER.value)

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash and self.password_hash.strip())

    @property
    def google_linked(self) -> bool:
        return bool(self.google_id and self.google_id.strip())

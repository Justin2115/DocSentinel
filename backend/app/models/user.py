from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

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
        default="UPLOAD_MAKER",
        server_default="UPLOAD_MAKER",
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
    def is_admin(self) -> bool:
        return (self.role or "").strip().upper() == "ADMIN"

    @property
    def is_maker(self) -> bool:
        return (self.role or "").strip().upper() in ("UPLOAD_MAKER", "MAKER", "USER", "ADMIN")

    @property
    def is_checker(self) -> bool:
        return (self.role or "").strip().upper() in ("UPLOAD_CHECKER", "CHECKER", "ADMIN")
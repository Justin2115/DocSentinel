from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FolderPermission(Base):
    __tablename__ = "folder_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    folder_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    can_view: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_upload: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("folder_name", "role", name="uq_folder_permission_folder_role"),
    )

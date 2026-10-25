from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    department: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_resource: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

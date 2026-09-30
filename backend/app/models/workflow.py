from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkflowSetting(Base):
    __tablename__ = "workflow_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=80.0, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )


class WorkflowRule(Base):
    __tablename__ = "workflow_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(String(50), default="warning", nullable=False)  # warning, security, claims
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    route: Mapped[str] = mapped_column(String(150), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    threshold: Mapped[float] = mapped_column(Float, default=80.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

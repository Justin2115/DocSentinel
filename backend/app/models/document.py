from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    stored_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    file_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    file_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    file_size: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    document_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    department: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )

    status: Mapped[str | None] = mapped_column(
        String(50),
        default="UPLOADED",
        nullable=True,
    )

    uploaded_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    assigned_checker: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    overall_confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        onupdate=func.now(),
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    @property
    def created_at(self) -> datetime | None:
        return self.uploaded_at

    # Relationships
    uploader: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[uploaded_by],
        primaryjoin="Document.uploaded_by == User.id",
    )

    checker: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[assigned_checker],
        primaryjoin="Document.assigned_checker == User.id",
    )

    pages: Mapped[list["DocumentPage"]] = relationship(
        "DocumentPage",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentPage.page_number",
    )

    ocr_results: Mapped[list["OCRResult"]] = relationship(
        "OCRResult",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    extracted_fields: Mapped[list["ExtractedField"]] = relationship(
        "ExtractedField",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    review_items: Mapped[list["ReviewQueue"]] = relationship(
        "ReviewQueue",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    embedding_index: Mapped[list["EmbeddingIndex"]] = relationship(
        "EmbeddingIndex",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    page_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    width: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    height: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Relationships
    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="pages",
    )

    ocr_results: Mapped[list["OCRResult"]] = relationship(
        "OCRResult",
        back_populates="page",
        cascade="all, delete-orphan",
    )

    extracted_fields: Mapped[list["ExtractedField"]] = relationship(
        "ExtractedField",
        back_populates="page",
    )


class OCRResult(Base):
    __tablename__ = "ocr_results"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    page_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("document_pages.id", ondelete="CASCADE"),
        nullable=True,
    )

    extracted_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    ocr_engine: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=True,
    )

    # Relationships
    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="ocr_results",
    )

    page: Mapped["DocumentPage | None"] = relationship(
        "DocumentPage",
        back_populates="ocr_results",
    )


class ExtractedField(Base):
    __tablename__ = "extracted_fields"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    page_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("document_pages.id", ondelete="SET NULL"),
        nullable=True,
    )

    field_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    field_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    original_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    corrected_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_verified: Mapped[bool | None] = mapped_column(
        Boolean,
        default=False,
        nullable=True,
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=True,
    )

    # Relationships
    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="extracted_fields",
    )

    page: Mapped["DocumentPage | None"] = relationship(
        "DocumentPage",
        back_populates="extracted_fields",
    )


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    status: Mapped[str | None] = mapped_column(
        String(50),
        default="pending",
        nullable=True,
    )

    assigned_to: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    reviewed_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=True,
    )

    # Relationships
    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="review_items",
    )


from app.models.embedding import EmbeddingIndex  # noqa: E402,F401
from app.models.user import User  # noqa: E402,F401


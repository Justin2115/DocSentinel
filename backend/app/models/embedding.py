from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document import Document


class EmbeddingIndex(Base):
    __tablename__ = "embedding_index"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "page_number",
            "chunk_index",
            name="uq_embedding_chunk",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    vector_db_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=True,
    )

    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="embedding_index",
    )

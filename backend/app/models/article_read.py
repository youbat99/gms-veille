import uuid
from datetime import datetime
from sqlalchemy import Boolean, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, UUIDMixin


class ArticleRead(Base, UUIDMixin):
    """Suivi de lecture des articles par utilisateur (lu/non lu + étoile)."""
    __tablename__ = "article_reads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    starred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "article_id", name="uq_article_read_user_article"),
    )

import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, ForeignKey, UniqueConstraint, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin, UUIDMixin


class RssArticle(Base, UUIDMixin, TimestampMixin):
    """Article collecté — toutes sources confondues (RSS, sitemap, SerpAPI, Playwright)"""
    __tablename__ = "rss_articles"

    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_sources.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")

    # Méthode de collecte — "rss" | "sitemap" | "serpapi" | "playwright"
    collection_method: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Pipeline state machine
    # "pending"   → collecté, extraction en attente
    # "extracted" → Newspaper4k/Trafilatura a extrait le contenu
    # "matched"   → keywords matchés, envoyé en HITL
    # "no_match"  → aucun keyword trouvé, archivé
    # "failed"    → extraction impossible (paywall, timeout, erreur)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # raison du failed

    # Données extraites (remplies par le worker d'extraction)
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    detected_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    # Retry logic
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps pipeline (conservés pour compatibilité)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped["MediaSource"] = relationship(back_populates="rss_articles")

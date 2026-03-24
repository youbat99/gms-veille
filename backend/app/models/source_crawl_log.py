import uuid
from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class SourceCrawlLog(Base):
    """Historique des crawls par source — un enregistrement par exécution."""
    __tablename__ = "source_crawl_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    # "scheduled" | "manual"

    new_articles: Mapped[int] = mapped_column(Integer, default=0)
    # articles réellement nouveaux (pas déjà en DB)
    total_found: Mapped[int] = mapped_column(Integer, default=0)
    # articles trouvés dans le feed (avant dédup)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    # articles déjà présents (total_found - new_articles)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped["MediaSource"] = relationship("MediaSource")

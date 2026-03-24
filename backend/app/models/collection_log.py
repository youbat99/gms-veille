import uuid
from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class CollectionLog(Base):
    """Historique de chaque exécution de scraping."""
    __tablename__ = "collection_log"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    # revue_id nullable → conservé même si la revue est supprimée
    revue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("revues.id", ondelete="SET NULL"),
        nullable=True,
    )
    revue_name: Mapped[str] = mapped_column(String(255), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trigger: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual"
    )   # "manual" | "scheduled"
    tbs: Mapped[str | None] = mapped_column(String(32), nullable=True)
    collected: Mapped[int]  = mapped_column(Integer, default=0)
    errors: Mapped[int]     = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    filtered_old: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="success"
    )   # "success" | "partial" | "error"
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Paramètres SerpAPI envoyés ──────────────────────────────────────
    engine:      Mapped[str | None] = mapped_column(String(32),  nullable=True)
    gl:          Mapped[str | None] = mapped_column(String(8),   nullable=True)
    language:    Mapped[str | None] = mapped_column(String(8),   nullable=True)
    sort_by:     Mapped[str | None] = mapped_column(String(16),  nullable=True)
    as_qdr:      Mapped[str | None] = mapped_column(String(16),  nullable=True)
    safe_search: Mapped[bool | None] = mapped_column(Boolean,    nullable=True)
    num_results: Mapped[int | None]  = mapped_column(Integer,    nullable=True)

    # ── Articles trouvés (URL + titre + date) ───────────────────────────
    articles_found: Mapped[list | None] = mapped_column(JSONB, nullable=True)

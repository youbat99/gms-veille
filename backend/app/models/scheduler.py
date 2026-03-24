import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Integer, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class SchedulerSlotKeyword(Base):
    """Association slot ↔ keyword (vide = tous les keywords de la revue)."""
    __tablename__ = "scheduler_slot_keywords"

    slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scheduler_slot.id", ondelete="CASCADE"), primary_key=True
    )
    keyword_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True
    )


class SchedulerSlot(Base):
    __tablename__ = "scheduler_slot"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    revue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revues.id", ondelete="CASCADE"), nullable=False
    )
    hour:   Mapped[int]  = mapped_column(Integer, nullable=False)           # 0-23
    minute: Mapped[int]  = mapped_column(Integer, default=0, nullable=False) # 0-59
    label:  Mapped[str]  = mapped_column(String(64), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── SerpAPI params — tous configurables par créneau ──────────────────
    tbs:         Mapped[str]  = mapped_column(String(32),  default="qdr:d",        nullable=False)
    language:    Mapped[str]  = mapped_column(String(8),   default="fr",           nullable=False)
    num_results: Mapped[int]  = mapped_column(Integer,     default=100,            nullable=False)
    engine:      Mapped[str]  = mapped_column(String(32),  default="google_news", nullable=False)  # google_news (= google+tbm=nws en interne)
    gl:          Mapped[str]  = mapped_column(String(8),   default="ma",          nullable=False)  # pays (ISO 3166-1 alpha-2)
    sort_by:     Mapped[str]  = mapped_column(String(16),  default="date",        nullable=False)  # relevance | date
    safe_search: Mapped[bool] = mapped_column(Boolean,     default=True,           nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Mots-clés associés (vide = tous les keywords de la revue) ────────
    slot_keywords: Mapped[list["SchedulerSlotKeyword"]] = relationship(
        "SchedulerSlotKeyword", cascade="all, delete-orphan", lazy="selectin"
    )

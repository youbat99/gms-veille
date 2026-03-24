"""
Modèles pour la newsletter / revue de presse par email.
"""
import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin, TimestampMixin


class NewsletterConfig(Base, UUIDMixin, TimestampMixin):
    """Configuration de la newsletter par revue (1 config = 1 revue)."""
    __tablename__ = "newsletter_configs"

    revue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("revues.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    schedule_hour: Mapped[int] = mapped_column(sa.Integer, default=8, nullable=False)
    schedule_minute: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    # Destinataires supplémentaires (en plus de l'email du client)
    extra_recipients: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    include_client_email: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    # Template du sujet — variables : {revue}, {date}
    subject_template: Mapped[str] = mapped_column(
        sa.String(300),
        default="Revue de presse · {revue} · {date}",
        nullable=False,
    )
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    revue: Mapped["Revue"] = relationship("Revue", foreign_keys=[revue_id])  # type: ignore


class EmailLog(Base, UUIDMixin, TimestampMixin):
    """Historique des envois de newsletter."""
    __tablename__ = "email_logs"

    revue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("revues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sent_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    recipients: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    article_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    period_from: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    period_to: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    subject: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    # "sent" | "error"
    status: Mapped[str] = mapped_column(sa.String(20), default="sent", nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # "manual" | "scheduled" | "test" | "critical"
    triggered_by: Mapped[str] = mapped_column(sa.String(20), default="manual", nullable=False)
    # Inbox fields (added sprint 3)
    html_snapshot: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    article_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False, server_default="[]")
    is_critical: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    revue: Mapped["Revue"] = relationship("Revue", foreign_keys=[revue_id])  # type: ignore

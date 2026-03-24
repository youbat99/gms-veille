import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, ForeignKey, Enum, DateTime, Text, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin, UUIDMixin
import enum


class KeywordType(str, enum.Enum):
    transversal = "transversal"  # partagé entre clients → coût réduit
    exclusive = "exclusive"       # propre au client → coût plein


class Revue(Base, UUIDMixin, TimestampMixin):
    """Revue de presse : groupe de mots clés pour un client"""
    __tablename__ = "revues"

    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    client: Mapped["Client"] = relationship(back_populates="revues")
    revue_keywords: Mapped[list["RevueKeyword"]] = relationship(back_populates="revue", cascade="all, delete-orphan")
    articles: Mapped[list["Article"]] = relationship(back_populates="revue")
    user_accesses: Mapped[list["UserAccount"]] = relationship(back_populates="revue")


class Keyword(Base, UUIDMixin, TimestampMixin):
    """Mot clé de recherche"""
    __tablename__ = "keywords"

    term: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str | None] = mapped_column(Text)        # Requête booléenne auto-générée ou manuelle
    query_json: Mapped[dict | None] = mapped_column(JSONB)  # Structure visuelle du query builder
    language: Mapped[str] = mapped_column(String(10), default="fr")  # fr, ar, en
    type: Mapped[KeywordType] = mapped_column(Enum(KeywordType), default=KeywordType.transversal)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    revue_keywords: Mapped[list["RevueKeyword"]] = relationship(back_populates="keyword")


class RevueKeyword(Base, TimestampMixin):
    """Association Revue ↔ Keyword — config SerpAPI par keyword (override du slot)"""
    __tablename__ = "revue_keywords"

    revue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("revues.id"), primary_key=True)
    keyword_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("keywords.id"), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Config SerpAPI propre au keyword (nullable = utilise la config globale du slot)
    tbs:         Mapped[str | None] = mapped_column(String(16),  nullable=True)
    gl:          Mapped[str | None] = mapped_column(String(8),   nullable=True)
    language:    Mapped[str | None] = mapped_column(String(8),   nullable=True)
    num_results: Mapped[int | None] = mapped_column(Integer,     nullable=True)
    sort_by:     Mapped[str | None] = mapped_column(String(16),  nullable=True)
    safe_search: Mapped[bool | None] = mapped_column(Boolean,    nullable=True)

    revue: Mapped["Revue"] = relationship(back_populates="revue_keywords")
    keyword: Mapped["Keyword"] = relationship(back_populates="revue_keywords")

import uuid
from datetime import date, datetime
from sqlalchemy import String, Boolean, ForeignKey, Float, Date, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin, UUIDMixin


class ArticleCluster(Base, UUIDMixin, TimestampMixin):
    """
    Cluster d'articles similaires (même événement couvert par plusieurs sources).
    Un cluster = un événement réel → N articles de N médias différents.
    """
    __tablename__ = "article_clusters"

    revue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("revues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Titre représentatif (article source ou le plus pertinent)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Date de l'événement (date de publication majoritaire)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Nombre de sources dans ce cluster
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Mot-clé principal du cluster (pour le groupement)
    keyword_term: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relations
    revue: Mapped["Revue"] = relationship()
    members: Mapped[list["ArticleClusterMember"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )


class ArticleClusterMember(Base):
    """Liaison Article ↔ ArticleCluster avec score de similarité."""
    __tablename__ = "article_cluster_members"

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("article_clusters.id", ondelete="CASCADE"),
        primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"),
        primary_key=True, index=True
    )
    # Score Jaccard vs l'article source (0.0 → 1.0)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # True = article considéré comme la source originale de l'info
    is_source: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relations
    cluster: Mapped["ArticleCluster"] = relationship(back_populates="members")
    article: Mapped["Article"] = relationship()

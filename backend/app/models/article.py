import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Boolean, ForeignKey, Enum, Text, Float, JSON, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin, UUIDMixin


class ArticleStatus(str, enum.Enum):
    pending = "pending"       # collecté, en attente de validation
    error = "error"           # scraping échoué (lien conservé)
    in_review = "in_review"   # ouvert par un support
    approved = "approved"     # validé tel quel
    modified = "modified"     # modifié puis validé
    rejected = "rejected"     # écarté


class ScrapingError(str, enum.Enum):
    timeout = "timeout"
    not_found = "not_found"       # 404
    paywall = "paywall"
    js_rendered = "js_rendered"   # page nécessite JS
    other = "other"


class Tonality(str, enum.Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"


class Article(Base, UUIDMixin, TimestampMixin):
    """Article collecté via SerpAPI + Newspaper"""
    __tablename__ = "articles"

    # Relations
    revue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("revues.id"), nullable=False)
    keyword_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("keywords.id"), nullable=False)
    validated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"))

    # Source
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_domain: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Contenu brut (peut être null si scraping KO)
    title: Mapped[str | None] = mapped_column(String(1024))
    content: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(String(2048))      # image principale (newspaper4k)
    meta_description: Mapped[str | None] = mapped_column(Text)       # meta description (newspaper4k)

    # NLP
    summary: Mapped[str | None] = mapped_column(Text)
    summary_ar: Mapped[str | None] = mapped_column(Text)   # traduction arabe
    summary_en: Mapped[str | None] = mapped_column(Text)   # traduction anglais
    tonality: Mapped[Tonality | None] = mapped_column(Enum(Tonality))
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    relevance_score: Mapped[float | None] = mapped_column(Float)  # 0.0 → 1.0
    theme: Mapped[str | None] = mapped_column(String(50), nullable=True)   # politique|économie|société|sport|culture|international
    weak_signal: Mapped[bool] = mapped_column(Boolean, default=False)      # crise/signal faible détecté par le LLM
    entities_persons: Mapped[list | None] = mapped_column(JSON, default=list)   # personnes citées
    entities_orgs: Mapped[list | None] = mapped_column(JSON, default=list)      # organisations citées
    entities_places: Mapped[list | None] = mapped_column(JSON, default=list)    # lieux mentionnés
    key_themes: Mapped[list | None] = mapped_column(JSON, nullable=True)        # 3-5 thèmes principaux (Claude)
    market_impact: Mapped[str | None] = mapped_column(Text, nullable=True)      # impact décideurs économiques (Claude)

    # Keywords matchés (tous, pas seulement le principal)
    # Format : [{"id": "...", "term": "...", "score": 90.0}, ...]
    matched_keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Origine de la collecte
    collection_method: Mapped[str | None] = mapped_column(String(50), nullable=True)  # serpapi | rss | sitemap | manual

    # Statut HITL
    status: Mapped[ArticleStatus] = mapped_column(Enum(ArticleStatus), default=ArticleStatus.pending, index=True)
    scraping_error: Mapped[ScrapingError | None] = mapped_column(Enum(ScrapingError))
    manually_entered: Mapped[bool] = mapped_column(Boolean, default=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relations
    revue: Mapped["Revue"] = relationship(back_populates="articles")
    keyword: Mapped["Keyword"] = relationship()
    modification_logs: Mapped[list["ArticleModificationLog"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class ArticleModificationLog(Base, UUIDMixin, TimestampMixin):
    """Audit trail des modifications faites par le support"""
    __tablename__ = "article_modification_logs"

    article_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("articles.id"), nullable=False)
    modified_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    fields_changed: Mapped[list] = mapped_column(JSON, default=list)  # ["summary", "tonality", ...]
    original_values: Mapped[dict] = mapped_column(JSON, default=dict)
    new_values: Mapped[dict] = mapped_column(JSON, default=dict)

    article: Mapped["Article"] = relationship(back_populates="modification_logs")

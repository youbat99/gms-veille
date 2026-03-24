import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin, UUIDMixin


class MediaSource(Base, UUIDMixin, TimestampMixin):
    """Support média — source RSS crawlée en continu"""
    __tablename__ = "media_sources"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    rss_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    rss_type: Mapped[str] = mapped_column(String(50), default="natif")  # natif | google_news
    crawl_method: Mapped[str] = mapped_column(String(20), default="rss")  # rss | playwright
    language: Mapped[str] = mapped_column(String(5), default="ar", nullable=False)  # ar | fr | en
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)  # apparaît en premier dans la vue
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rss_articles: Mapped[list["RssArticle"]] = relationship(back_populates="source", cascade="all, delete-orphan")

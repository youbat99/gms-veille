from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
import uuid
import asyncio
import aiohttp
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import get_current_user, require_admin_plus
from app.models.client import Account, AccountRole, ClientMediaSource
from app.models.media_source import MediaSource
from app.models.rss_article import RssArticle
from app.services.rss_service import crawl_source

router = APIRouter(tags=["media-feed"])


class MediaSourceAssigned(BaseModel):
    id: str
    name: str
    base_url: str
    logo_url: Optional[str]
    language: str
    is_featured: bool
    article_count: int = 0

    class Config:
        from_attributes = True


class FeedArticle(BaseModel):
    id: str
    source_id: str
    source_name: str
    source_logo: Optional[str]
    source_url: str
    title: str
    url: str
    image_url: Optional[str]
    published_at: Optional[datetime]
    summary: Optional[str]
    author: Optional[str]


# ── Admin: manage client sources ─────────────────────────────────────────────

@router.get("/clients/{client_id}/media-sources")
async def list_client_sources(
    client_id: uuid.UUID,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    """Liste les sources assignées à un client."""
    result = await db.execute(
        select(MediaSource)
        .join(ClientMediaSource, ClientMediaSource.source_id == MediaSource.id)
        .where(ClientMediaSource.client_id == client_id)
        .order_by(MediaSource.name)
    )
    sources = result.scalars().all()
    return [{"id": str(s.id), "name": s.name, "base_url": s.base_url, "logo_url": s.logo_url, "language": s.language, "is_featured": s.is_featured} for s in sources]


@router.post("/clients/{client_id}/media-sources/{source_id}", status_code=201)
async def assign_source_to_client(
    client_id: uuid.UUID,
    source_id: uuid.UUID,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    """Assigne une source média à un client."""
    existing = await db.get(ClientMediaSource, (client_id, source_id))
    if existing:
        return {"ok": True}
    db.add(ClientMediaSource(client_id=client_id, source_id=source_id))
    await db.commit()
    return {"ok": True}


@router.delete("/clients/{client_id}/media-sources/{source_id}", status_code=204)
async def remove_source_from_client(
    client_id: uuid.UUID,
    source_id: uuid.UUID,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    """Retire une source d'un client."""
    await db.execute(
        delete(ClientMediaSource)
        .where(ClientMediaSource.client_id == client_id, ClientMediaSource.source_id == source_id)
    )
    await db.commit()


# ── Client: read their feed ───────────────────────────────────────────────────

@router.get("/media-feed", response_model=list[FeedArticle])
async def get_media_feed(
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retourne le dernier article de chaque source assignée au client de l'utilisateur.
    Super admin (pas de client_id) : voit toutes les sources actives."""
    is_admin = current_user.role in ("super_admin", "admin") or not current_user.client_id

    if is_admin:
        # Admin GMS : toutes les sources actives
        sources_result = await db.execute(
            select(MediaSource)
            .where(MediaSource.is_active == True)
            .order_by(MediaSource.is_featured.desc().nullslast(), MediaSource.name)
        )
    else:
        # Client : uniquement ses sources assignées et actives
        sources_result = await db.execute(
            select(MediaSource)
            .join(ClientMediaSource, ClientMediaSource.source_id == MediaSource.id)
            .where(
                ClientMediaSource.client_id == current_user.client_id,
                MediaSource.is_active == True,
            )
            .order_by(MediaSource.is_featured.desc().nullslast(), MediaSource.name)
        )
    sources = sources_result.scalars().all()

    if not sources:
        return []

    # For each source, get the latest enriched article (title + date available)
    feed = []
    for source in sources:
        art_result = await db.execute(
            select(RssArticle)
            .where(
                RssArticle.source_id == source.id,
                RssArticle.status.in_(["extracted", "matched", "no_match"]),
                RssArticle.title.isnot(None),
            )
            .order_by(RssArticle.published_at.desc().nullslast())
            .limit(1)
        )
        art = art_result.scalar_one_or_none()
        if art:
            feed.append(FeedArticle(
                id=str(art.id),
                source_id=str(source.id),
                source_name=source.name,
                source_logo=source.logo_url,
                source_url=source.base_url,
                title=art.title,
                url=art.url,
                image_url=art.image_url,
                published_at=art.published_at,
                summary=art.summary,
                author=art.author,
            ))

    return feed


@router.get("/media-feed/sources/{source_id}/articles", response_model=list[FeedArticle])
async def get_source_articles_feed(
    source_id: uuid.UUID,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    hours: int = Query(default=24, ge=1, le=8760),
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retourne les derniers articles d'une source (vérifie que le client y a accès)."""
    from datetime import timezone, timedelta
    if not current_user.client_id:
        raise HTTPException(400, "Compte non lié à un client")

    # Vérifier que la source est bien assignée au client
    assignment = await db.get(ClientMediaSource, (current_user.client_id, source_id))
    if not assignment:
        raise HTTPException(403, "Source non assignée à votre compte")

    source = await db.get(MediaSource, source_id)
    if not source:
        raise HTTPException(404, "Source introuvable")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(RssArticle)
        .where(
            RssArticle.source_id == source_id,
            RssArticle.published_at >= since,
            RssArticle.status.in_(["extracted", "matched", "no_match"]),
            RssArticle.title.isnot(None),
        )
        .order_by(RssArticle.published_at.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    articles = result.scalars().all()

    return [FeedArticle(
        id=str(a.id),
        source_id=str(source.id),
        source_name=source.name,
        source_logo=source.logo_url,
        source_url=source.base_url,
        title=a.title,
        url=a.url,
        image_url=a.image_url,
        published_at=a.published_at,
        summary=a.summary,
        author=a.author,
    ) for a in articles]


@router.post("/media-feed/sources/{source_id}/crawl", response_model=list[FeedArticle])
async def crawl_and_refresh(
    source_id: uuid.UUID,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Déclenche un crawl immédiat de la source puis retourne les derniers articles."""
    is_admin = current_user.role in ("super_admin", "admin") or not current_user.client_id

    if not is_admin:
        if not current_user.client_id:
            raise HTTPException(403, "Accès refusé")
        assignment = await db.get(ClientMediaSource, (current_user.client_id, source_id))
        if not assignment:
            raise HTTPException(403, "Source non assignée à votre compte")

    source = await db.get(MediaSource, source_id)
    if not source:
        raise HTTPException(404, "Source introuvable")

    # Crawl immédiat (fire & store, pas de timeout bloquant)
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as http_session:
            raw_articles = await asyncio.wait_for(
                crawl_source(http_session, source),
                timeout=15.0,
            )

        for art in raw_articles:
            existing = await db.execute(
                select(RssArticle.id).where(RssArticle.url_hash == art["url_hash"])
            )
            if existing.scalar_one_or_none():
                continue
            db.add(RssArticle(
                source_id=source.id,
                url=art["url"],
                url_hash=art["url_hash"],
                collection_method="rss",
                status="pending",
            ))

        source.last_crawled_at = datetime.utcnow()
        await db.commit()
    except (asyncio.TimeoutError, Exception):
        # Crawl timeout ou erreur réseau → on retourne quand même les articles existants
        pass

    # Retourner les 30 derniers articles enrichis (incluant les nouveaux si déjà passés par le worker)
    result = await db.execute(
        select(RssArticle)
        .where(
            RssArticle.source_id == source_id,
            RssArticle.status.in_(["extracted", "matched", "no_match"]),
            RssArticle.title.isnot(None),
        )
        .order_by(RssArticle.published_at.desc().nullslast())
        .limit(30)
    )
    articles = result.scalars().all()

    return [FeedArticle(
        id=str(a.id),
        source_id=str(source.id),
        source_name=source.name,
        source_logo=source.logo_url,
        source_url=source.base_url,
        title=a.title,
        url=a.url,
        image_url=a.image_url,
        published_at=a.published_at,
        summary=a.summary,
        author=a.author,
    ) for a in articles]


@router.get("/media-feed/sources")
async def get_my_sources(
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Liste les sources assignées au client. Super admin voit toutes les sources actives."""
    is_admin = current_user.role in ("super_admin", "admin") or not current_user.client_id

    if is_admin:
        result = await db.execute(
            select(MediaSource)
            .where(MediaSource.is_active == True)
            .order_by(MediaSource.is_featured.desc().nullslast(), MediaSource.name)
        )
    else:
        if not current_user.client_id:
            return []
        result = await db.execute(
            select(MediaSource)
            .join(ClientMediaSource, ClientMediaSource.source_id == MediaSource.id)
            .where(
                ClientMediaSource.client_id == current_user.client_id,
                MediaSource.is_active == True,
            )
            .order_by(MediaSource.is_featured.desc().nullslast(), MediaSource.name)
        )
    sources = result.scalars().all()
    return [{"id": str(s.id), "name": s.name, "base_url": s.base_url, "logo_url": s.logo_url, "language": s.language, "is_featured": s.is_featured} for s in sources]

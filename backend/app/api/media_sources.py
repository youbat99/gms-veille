import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc, text
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.models.media_source import MediaSource
from app.models.rss_article import RssArticle
from app.models.client import Client, ClientMediaSource
from app.services.rss_service import detect_rss, get_logo_url, get_favicon_url, get_base_url


def _make_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def _health_status(last_crawled_at: datetime | None, last_collected_at: datetime | None) -> str:
    """Calcule le statut de santé d'une source : ok | warning | critical."""
    now = datetime.now(timezone.utc)
    if last_crawled_at is None:
        return "new"
    crawled_ago = (now - last_crawled_at.replace(tzinfo=timezone.utc) if last_crawled_at.tzinfo is None else now - last_crawled_at).total_seconds() / 3600
    if crawled_ago > 24:
        return "critical"
    if crawled_ago > 12:
        return "warning"
    return "ok"

router = APIRouter(prefix="/api/media-sources", tags=["media-sources"])


# ── Schémas ────────────────────────────────────────────────────────────

class MediaSourceOut(BaseModel):
    id: uuid.UUID
    name: str
    base_url: str
    rss_url: str
    logo_url: Optional[str]
    favicon_url: Optional[str]
    rss_type: str
    crawl_method: str = "rss"   # rss | playwright
    language: str = "ar"
    is_featured: bool = False
    is_active: bool
    last_crawled_at: Optional[str]
    last_collected_at: Optional[str] = None
    article_count: Optional[int] = None
    last_new_articles: Optional[int] = None   # nouveaux articles du dernier crawl
    health_status: str = "ok"   # ok | warning | critical

    class Config:
        from_attributes = True


class RssArticleOut(BaseModel):
    id: uuid.UUID
    title: Optional[str]
    url: str
    published_at: Optional[str]
    summary: Optional[str]
    image_url: Optional[str]
    author: Optional[str]
    source_name: str
    source_logo: Optional[str]
    source_favicon: Optional[str]
    source_base_url: Optional[str]

    class Config:
        from_attributes = True


class AddSourceRequest(BaseModel):
    url: str
    name: Optional[str] = None
    language: Optional[str] = None  # ar | fr | en — auto-détecté si absent


class UpdateSourceRequest(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    rss_url: Optional[str] = None          # si modifié → reset last_crawled_at
    crawl_method: Optional[str] = None     # rss | playwright
    language: Optional[str] = None         # ar | fr | en
    is_featured: Optional[bool] = None
    is_active: Optional[bool] = None


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("", response_model=list[MediaSourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    """Liste toutes les sources avec leur nombre d'articles et statut de santé."""
    from app.models.source_crawl_log import SourceCrawlLog

    # Sous-requête : new_articles du dernier crawl de chaque source
    last_crawl_sq = (
        select(
            SourceCrawlLog.source_id,
            SourceCrawlLog.new_articles.label("last_new_articles"),
        )
        .distinct(SourceCrawlLog.source_id)
        .order_by(SourceCrawlLog.source_id, SourceCrawlLog.crawled_at.desc())
        .subquery()
    )

    result = await db.execute(
        select(
            MediaSource,
            func.count(RssArticle.id).label("article_count"),
            func.max(RssArticle.collected_at).label("last_collected_at"),
            last_crawl_sq.c.last_new_articles,
        )
        .outerjoin(RssArticle, RssArticle.source_id == MediaSource.id)
        .outerjoin(last_crawl_sq, last_crawl_sq.c.source_id == MediaSource.id)
        .where(MediaSource.is_active == True)
        .group_by(MediaSource.id, last_crawl_sq.c.last_new_articles)
        .order_by(MediaSource.is_featured.desc(), MediaSource.name)
    )
    rows = result.all()
    out = []
    for source, count, last_collected_at, last_new_articles in rows:
        d = MediaSourceOut(
            id=source.id,
            name=source.name,
            base_url=source.base_url,
            rss_url=source.rss_url,
            logo_url=source.logo_url,
            favicon_url=get_favicon_url(source.base_url),
            rss_type=source.rss_type,
            crawl_method=source.crawl_method,
            language=source.language,
            is_featured=source.is_featured,
            is_active=source.is_active,
            last_crawled_at=source.last_crawled_at.isoformat() if source.last_crawled_at else None,
            last_collected_at=last_collected_at.isoformat() if last_collected_at else None,
            article_count=count,
            last_new_articles=last_new_articles,
            health_status=_health_status(source.last_crawled_at, last_collected_at),
        )
        out.append(d)
    return out


@router.get("/articles/recent")
async def get_recent_articles(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    hours: Optional[int] = Query(default=None, ge=1, le=8760),
    search: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default="all"),   # "pending" | "failed" | "matched" | "no_match" | "all"
    language: Optional[str] = Query(default=None),      # "ar" | "fr" | "en"
    db: AsyncSession = Depends(get_db),
):
    """Derniers articles RSS, filtrables par statut pipeline."""
    from datetime import datetime, timedelta, timezone

    PIPELINE_STATUSES = {"pending", "extracted", "matched", "no_match", "failed"}

    if status in PIPELINE_STATUSES:
        visible_statuses = [status]
    else:
        # "all" = tout sauf pending (comportement legacy)
        visible_statuses = ["extracted", "matched", "no_match"]

    title_required = status not in {"pending", "failed"}

    q = (
        select(RssArticle, MediaSource)
        .outerjoin(MediaSource, RssArticle.source_id == MediaSource.id)  # LEFT JOIN — inclut SerpAPI (source_id=None)
        .where(RssArticle.status.in_(visible_statuses))
    )
    if title_required:
        q = q.where(RssArticle.title.isnot(None))
    if hours is not None:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        q = q.where(RssArticle.published_at >= since)
    if search:
        # Full-text search via index GIN tsvector (50-200x plus rapide que ILIKE)
        # Fallback sur ILIKE si le terme contient des caractères spéciaux tsquery
        try:
            from sqlalchemy import func as _func
            _vec = _func.to_tsvector("simple",
                _func.coalesce(RssArticle.title, "") + " " + _func.coalesce(RssArticle.summary, "")
            )
            _query = _func.plainto_tsquery("simple", search)
            q = q.where(_vec.op("@@")(_query))
        except Exception:
            q = q.where(
                RssArticle.title.ilike(f"%{search}%") |
                RssArticle.summary.ilike(f"%{search}%")
            )
    if language:
        q = q.where(MediaSource.language == language)
    q = q.order_by(desc(RssArticle.published_at).nullslast(), desc(RssArticle.collected_at)).offset(offset).limit(limit)

    result = await db.execute(q)
    rows = result.all()

    return [
        {
            "id": str(a.id),
            "title": a.title,
            "url": a.url,
            "published_at": a.published_at.isoformat() if a.published_at else None,
            "collected_at": a.collected_at.isoformat() if a.collected_at else None,
            "summary": a.summary,
            "image_url": a.image_url,
            "author": a.author,
            "source_name": s.name if s else None,
            "source_logo": s.logo_url if s else None,
            "source_favicon": get_favicon_url(s.base_url) if s else None,
            "source_base_url": s.base_url if s else None,
            "source_language": s.language if s else None,  # "ar" | "fr" | "en"
            "collection_method": a.collection_method,
            "status": a.status,
            "matched_keywords": getattr(a, "matched_keywords", None),
            "extraction_error": getattr(a, "extraction_error", None),
            "retry_count": getattr(a, "retry_count", None),
        }
        for a, s in rows
    ]


@router.get("/check-url")
async def check_url(url: str = Query(..., description="URL à vérifier"), db: AsyncSession = Depends(get_db)):
    """
    Vérifie si une URL est déjà connue :
    - article_exists : l'URL est déjà dans rss_articles
    - source_exists  : la source parente est déjà dans media_sources
    """
    from urllib.parse import urlparse
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    url_hash = _make_hash(url)
    base = get_base_url(url)

    # Vérif article
    art_row = await db.execute(
        select(RssArticle.id, RssArticle.title, RssArticle.status, RssArticle.collected_at)
        .where(RssArticle.url_hash == url_hash)
    )
    art = art_row.first()

    # Vérif source — uniquement les sources ACTIVES (is_active=True)
    src_row = await db.execute(
        select(MediaSource.id, MediaSource.name, MediaSource.last_crawled_at)
        .where(MediaSource.base_url == base, MediaSource.is_active == True)
    )
    src = src_row.first()

    return {
        "url": url,
        "base_url": base,
        "article_exists": art is not None,
        "article": {
            "id": str(art.id),
            "title": art.title,
            "status": art.status,
            "collected_at": art.collected_at.isoformat() if art.collected_at else None,
        } if art else None,
        "source_exists": src is not None,
        "source": {
            "id": str(src.id),
            "name": src.name,
            "last_crawled_at": src.last_crawled_at.isoformat() if src.last_crawled_at else None,
        } if src else None,
    }


@router.get("/{source_id}/articles", response_model=list[RssArticleOut])
async def get_source_articles(
    source_id: uuid.UUID,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    hours: int = Query(default=24, ge=1, le=8760),
    db: AsyncSession = Depends(get_db),
):
    """Derniers articles d'une source (dernières N heures, 24h par défaut)."""
    source = await db.get(MediaSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable")

    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Tri : COALESCE(published_at, collected_at) DESC
    # → les articles sans published_at (encore pending) remontent via collected_at
    sort_key = func.coalesce(RssArticle.published_at, RssArticle.collected_at).desc()

    result = await db.execute(
        select(RssArticle)
        .where(
            RssArticle.source_id == source_id,
            RssArticle.collected_at >= since,
        )
        .order_by(sort_key)
        .offset(offset)
        .limit(limit)
    )
    articles = result.scalars().all()

    # Fallback : si aucun article dans la fenêtre, retourner les derniers sans limite de temps
    if not articles and offset == 0:
        result = await db.execute(
            select(RssArticle)
            .where(RssArticle.source_id == source_id)
            .order_by(sort_key)
            .limit(limit)
        )
        articles = result.scalars().all()

    return [
        RssArticleOut(
            id=a.id,
            title=a.title,
            url=a.url,
            published_at=a.published_at.isoformat() if a.published_at else None,
            summary=a.summary,
            image_url=a.image_url,
            author=a.author,
            source_name=source.name,
            source_logo=source.logo_url,
            source_favicon=get_favicon_url(source.base_url),
            source_base_url=source.base_url,
        )
        for a in articles
    ]


@router.get("/revue/search", response_model=list[RssArticleOut])
async def search_rss_articles(
    keywords: str = Query(..., description="Mots-clés séparés par des virgules"),
    hours: int = Query(default=24, description="Dernières X heures"),
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Recherche d'articles RSS par mots-clés."""
    from sqlalchemy import or_, and_
    from datetime import datetime, timedelta

    terms = [k.strip() for k in keywords.split(",") if k.strip()]
    if not terms:
        raise HTTPException(status_code=400, detail="Au moins un mot-clé requis")

    since = datetime.utcnow() - timedelta(hours=hours)

    conditions = or_(*[
        or_(
            RssArticle.title.ilike(f"%{t}%"),
            RssArticle.summary.ilike(f"%{t}%"),
            RssArticle.content.ilike(f"%{t}%"),   # texte intégral — non exposé au frontend
        )
        for t in terms
    ])

    result = await db.execute(
        select(RssArticle, MediaSource)
        .join(MediaSource, RssArticle.source_id == MediaSource.id)
        .where(and_(
            conditions,
            RssArticle.collected_at >= since,
        ))
        .order_by(RssArticle.published_at.desc().nullslast())
        .limit(limit)
    )
    rows = result.all()

    return [
        RssArticleOut(
            id=a.id,
            title=a.title,
            url=a.url,
            published_at=a.published_at.isoformat() if a.published_at else None,
            summary=a.summary,
            image_url=a.image_url,
            author=a.author,
            source_name=s.name,
            source_logo=s.logo_url,
            source_favicon=get_favicon_url(s.base_url),
            source_base_url=s.base_url,
        )
        for a, s in rows
    ]


@router.post("", response_model=MediaSourceOut)
async def add_source(body: AddSourceRequest, db: AsyncSession = Depends(get_db)):
    """Ajoute une nouvelle source — détecte automatiquement le RSS."""
    url = body.url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    base_url = get_base_url(url)

    # Vérifier si déjà en base (y compris sources désactivées)
    existing_result = await db.execute(select(MediaSource).where(MediaSource.base_url == base_url))
    existing = existing_result.scalar_one_or_none()
    if existing:
        if existing.is_active:
            raise HTTPException(status_code=409, detail="Source déjà existante")
        # Source supprimée (is_active=False) → la réactiver
        existing.is_active = True
        await db.commit()
        await db.refresh(existing)
        return MediaSourceOut(
            id=existing.id,
            name=existing.name,
            base_url=existing.base_url,
            rss_url=existing.rss_url,
            logo_url=existing.logo_url,
            favicon_url=get_favicon_url(existing.base_url),
            rss_type=existing.rss_type,
            crawl_method=existing.crawl_method,
            language=existing.language,
            is_featured=existing.is_featured or False,
            is_active=True,
            last_crawled_at=existing.last_crawled_at.isoformat() if existing.last_crawled_at else None,
            article_count=0,
        )

    # Détecter RSS
    rss_info = await detect_rss(url)

    from urllib.parse import urlparse
    domain = urlparse(base_url).netloc
    name = body.name or domain.replace("www.", "").split(".")[0].capitalize()

    # Auto-détection de la langue depuis l'URL si non fournie
    def _detect_language(url: str, provided: str | None) -> str:
        if provided and provided in ("ar", "fr", "en"):
            return provided
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("fr.") or url.rstrip("/").endswith(".fr"):
            return "fr"
        if netloc.startswith("en."):
            return "en"
        return "ar"

    language = _detect_language(base_url, body.language)

    # Résultat detect_rss : {"rss_url", "rss_type", "crawl_method"}
    if rss_info:
        crawl_method = rss_info.get("crawl_method", "rss")
        rss_url_val  = rss_info["rss_url"]
        rss_type_val = rss_info["rss_type"]
    else:
        # Dernier recours : Playwright
        crawl_method = "playwright"
        rss_url_val  = base_url
        rss_type_val = "playwright"

    source = MediaSource(
        name=name,
        base_url=base_url,
        rss_url=rss_url_val,
        logo_url=get_logo_url(base_url),
        rss_type=rss_type_val,
        crawl_method=crawl_method,
        language=language,
        is_active=True,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    return MediaSourceOut(
        id=source.id,
        name=source.name,
        base_url=source.base_url,
        rss_url=source.rss_url,
        logo_url=source.logo_url,
        favicon_url=get_favicon_url(source.base_url),
        rss_type=source.rss_type,
        crawl_method=source.crawl_method,
        language=source.language,
        is_featured=False,
        is_active=source.is_active,
        last_crawled_at=None,
        article_count=0,
    )


@router.get("/crawl-history", response_model=list[dict])
async def crawl_history(
    limit: int = Query(default=200, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Historique des crawls par source :
    pour chaque source active, retourne le nombre d'articles et les stats de collecte.
    Trié par dernier crawl décroissant.
    """
    from app.models.source_crawl_log import SourceCrawlLog

    # Sous-requête : new_articles du dernier crawl de chaque source
    last_crawl_sq = (
        select(
            SourceCrawlLog.source_id,
            SourceCrawlLog.new_articles.label("last_new_articles"),
        )
        .distinct(SourceCrawlLog.source_id)
        .order_by(SourceCrawlLog.source_id, SourceCrawlLog.crawled_at.desc())
        .subquery()
    )

    result = await db.execute(
        select(
            MediaSource.id,
            MediaSource.name,
            MediaSource.base_url,
            MediaSource.logo_url,
            MediaSource.language,
            MediaSource.last_crawled_at,
            func.count(RssArticle.id).label("total_articles"),
            func.count(RssArticle.id).filter(RssArticle.enriched_at.is_not(None)).label("enriched"),
            func.count(RssArticle.id).filter(RssArticle.matched_at.is_not(None)).label("matched"),
            func.max(RssArticle.collected_at).label("last_collected_at"),
            func.max(RssArticle.published_at).label("last_published_at"),
            last_crawl_sq.c.last_new_articles,
        )
        .outerjoin(RssArticle, RssArticle.source_id == MediaSource.id)
        .outerjoin(last_crawl_sq, last_crawl_sq.c.source_id == MediaSource.id)
        .where(MediaSource.is_active == True)
        .group_by(MediaSource.id, last_crawl_sq.c.last_new_articles)
        .order_by(MediaSource.last_crawled_at.desc().nullslast())
        .limit(limit)
    )
    rows = result.all()
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "base_url": row.base_url,
            "logo_url": row.logo_url,
            "favicon_url": get_favicon_url(row.base_url),
            "language": row.language,
            "last_crawled_at": row.last_crawled_at.isoformat() if row.last_crawled_at else None,
            "last_collected_at": row.last_collected_at.isoformat() if row.last_collected_at else None,
            "last_published_at": row.last_published_at.isoformat() if row.last_published_at else None,
            "total_articles": row.total_articles or 0,
            "enriched": row.enriched or 0,
            "matched": row.matched or 0,
            "enrichment_rate": round((row.enriched or 0) / max(row.total_articles or 1, 1) * 100),
            "match_rate": round((row.matched or 0) / max(row.total_articles or 1, 1) * 100),
            "last_new_articles": row.last_new_articles,
        }
        for row in rows
    ]


@router.get("/failures")
async def get_failures(db: AsyncSession = Depends(get_db)):
    """
    Retourne les sources avec des articles en échec (no_content, timeout, etc.)
    triées par nombre d'échecs décroissant, avec les URLs problématiques.
    """
    # Agréger échecs par source
    agg = await db.execute(
        select(
            RssArticle.source_id,
            RssArticle.extraction_error,
            func.count(RssArticle.id).label("failure_count"),
        )
        .where(RssArticle.status == "failed")
        .group_by(RssArticle.source_id, RssArticle.extraction_error)
        .order_by(func.count(RssArticle.id).desc())
    )
    rows = agg.all()

    # Récupérer les sources concernées
    source_ids = list({r.source_id for r in rows})
    sources_res = await db.execute(
        select(MediaSource).where(MediaSource.id.in_(source_ids))
    )
    sources_map = {s.id: s for s in sources_res.scalars().all()}

    # Pour chaque source, récupérer les 10 dernières URLs en échec
    out: dict[str, dict] = {}
    for r in rows:
        sid = str(r.source_id)
        src = sources_map.get(r.source_id)
        if not src:
            continue
        if sid not in out:
            out[sid] = {
                "source_id": sid,
                "name": src.name,
                "base_url": src.base_url,
                "favicon_url": get_favicon_url(src.base_url),
                "crawl_method": src.crawl_method,
                "total_failures": 0,
                "errors": {},
                "sample_urls": [],
            }
        out[sid]["errors"][r.extraction_error or "unknown"] = r.failure_count
        out[sid]["total_failures"] += r.failure_count

    # Sample URLs (10 dernières par source)
    if source_ids:
        urls_res = await db.execute(
            select(
                RssArticle.id,
                RssArticle.source_id,
                RssArticle.url,
                RssArticle.extraction_error,
                RssArticle.retry_count,
                RssArticle.collected_at,
            )
            .where(
                RssArticle.status == "failed",
                RssArticle.source_id.in_(source_ids),
            )
            .order_by(RssArticle.collected_at.desc())
            .limit(len(source_ids) * 10)
        )
        seen_per_source: dict[str, int] = {}
        for u in urls_res.all():
            sid = str(u.source_id)
            if seen_per_source.get(sid, 0) >= 10:
                continue
            if sid in out:
                out[sid]["sample_urls"].append({
                    "id": str(u.id),
                    "url": u.url,
                    "error": u.extraction_error or "unknown",
                    "retry_count": u.retry_count or 0,
                    "collected_at": u.collected_at.isoformat() if u.collected_at else None,
                })
                seen_per_source[sid] = seen_per_source.get(sid, 0) + 1

    result = sorted(out.values(), key=lambda x: x["total_failures"], reverse=True)
    return result


@router.post("/failures/{rss_id}/retry")
async def retry_failed_article(rss_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Réinitialise un article RSS échoué en 'pending' pour re-extraction."""
    res = await db.execute(select(RssArticle).where(RssArticle.id == rss_id))
    art = res.scalar_one_or_none()
    if not art:
        raise HTTPException(404, "Article introuvable")
    if art.status != "failed":
        raise HTTPException(400, f"Article en statut '{art.status}', pas 'failed'")
    art.status = "pending"
    art.extraction_error = None
    art.retry_count = 0
    art.retry_after = None
    await db.commit()
    return {"ok": True, "id": str(rss_id), "url": art.url}


@router.post("/failures/source/{source_id}/retry-all")
async def retry_all_failed_for_source(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Réinitialise tous les articles échoués d'une source en 'pending'."""
    from sqlalchemy import update
    result = await db.execute(
        update(RssArticle)
        .where(RssArticle.source_id == source_id, RssArticle.status == "failed")
        .values(status="pending", extraction_error=None, retry_count=0, retry_after=None)
        .returning(RssArticle.id)
    )
    ids = result.fetchall()
    await db.commit()
    return {"ok": True, "retried": len(ids)}


@router.get("/{source_id}/crawl-log")
async def get_source_crawl_log(
    source_id: uuid.UUID,
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Retourne l'historique des crawls pour une source (20 derniers par défaut)."""
    from app.models.source_crawl_log import SourceCrawlLog
    result = await db.execute(
        select(SourceCrawlLog)
        .where(SourceCrawlLog.source_id == source_id)
        .order_by(SourceCrawlLog.crawled_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "crawled_at": log.crawled_at.isoformat(),
            "trigger": log.trigger,
            "new_articles": log.new_articles,
            "total_found": log.total_found,
            "duplicates": log.duplicates,
            "duration_ms": log.duration_ms,
        }
        for log in logs
    ]


@router.post("/{source_id}/crawl-now")
async def crawl_source_now(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Déclenche un crawl immédiat de la source (fire-and-forget)."""
    import asyncio
    from app.services.rss_service import crawl_source, get_favicon_url as _fav
    import aiohttp
    from app.models.rss_article import RssArticle as RssArticleModel
    from datetime import datetime, timezone

    source = await db.get(MediaSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable")

    saved = 0
    total_found = 0
    import time as _time
    t0 = _time.monotonic()
    try:
        method = getattr(source, "crawl_method", "rss")
        if method == "playwright":
            from app.services.rss_service import crawl_source_playwright
            articles = await crawl_source_playwright(source)
        elif method == "sitemap":
            from app.services.rss_service import crawl_source_sitemap
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as http_session:
                articles = await crawl_source_sitemap(http_session, source)
        else:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as http_session:
                articles = await crawl_source(http_session, source)

        total_found = len(articles)
        from sqlalchemy import select as sa_select
        for art in articles:
            existing = await db.execute(
                sa_select(RssArticleModel.id).where(RssArticleModel.url_hash == art["url_hash"])
            )
            if existing.scalar_one_or_none():
                continue
            db.add(RssArticleModel(
                source_id=source.id,
                url=art["url"],
                url_hash=art["url_hash"],
                collection_method=method or "rss",
                status="pending",
                title=art.get("rss_title"),
                image_url=art.get("rss_image"),
                published_at=art.get("rss_pub_date"),
            ))
            saved += 1

        source.last_crawled_at = datetime.now(timezone.utc)
        # Enregistrement du log de crawl
        from app.models.source_crawl_log import SourceCrawlLog
        db.add(SourceCrawlLog(
            source_id=source.id,
            trigger="manual",
            new_articles=saved,
            total_found=total_found,
            duplicates=total_found - saved,
            duration_ms=int((_time.monotonic() - t0) * 1000),
        ))
        await db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"saved": saved, "total_found": total_found, "source": source.name}


@router.patch("/{source_id}", response_model=MediaSourceOut)
async def update_source(
    source_id: uuid.UUID,
    body: UpdateSourceRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Modifie une source media.
    Si rss_url ou base_url change → last_crawled_at remis à NULL
    (la source sera re-crawlée en priorité comme une nouvelle source).
    """
    source = await db.get(MediaSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable")

    # Détecter si une URL critique change (RSS ou base) → reset crawl
    url_changed = False

    if body.name is not None:
        source.name = body.name.strip()

    if body.base_url is not None:
        new_base = body.base_url.strip().rstrip("/")
        if not new_base.startswith("http"):
            new_base = "https://" + new_base
        if new_base != source.base_url:
            # Vérifier qu'aucune autre source n'a cette base_url
            conflict = await db.execute(
                select(MediaSource.id).where(
                    MediaSource.base_url == new_base,
                    MediaSource.id != source_id,
                )
            )
            if conflict.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="Cette URL est déjà utilisée par une autre source")
            source.base_url = new_base
            source.logo_url = get_logo_url(new_base)
            url_changed = True

    if body.rss_url is not None:
        new_rss = body.rss_url.strip()
        if not new_rss.startswith("http"):
            new_rss = "https://" + new_rss
        if new_rss != source.rss_url:
            source.rss_url = new_rss
            url_changed = True

    if body.crawl_method is not None and body.crawl_method in ("rss", "sitemap", "playwright"):
        source.crawl_method = body.crawl_method

    if body.language is not None and body.language in ("ar", "fr", "en"):
        source.language = body.language

    if body.is_featured is not None:
        source.is_featured = body.is_featured

    if body.is_active is not None:
        source.is_active = body.is_active

    # Si une URL critique a changé → priorité de crawl maximale
    if url_changed:
        source.last_crawled_at = None

    await db.commit()
    await db.refresh(source)

    # Compter les articles
    count_res = await db.execute(
        select(func.count(RssArticle.id)).where(RssArticle.source_id == source.id)
    )
    article_count = count_res.scalar() or 0

    return MediaSourceOut(
        id=source.id,
        name=source.name,
        base_url=source.base_url,
        rss_url=source.rss_url,
        logo_url=source.logo_url,
        favicon_url=get_favicon_url(source.base_url),
        rss_type=source.rss_type,
        crawl_method=source.crawl_method,
        language=source.language,
        is_featured=source.is_featured,
        is_active=source.is_active,
        last_crawled_at=source.last_crawled_at.isoformat() if source.last_crawled_at else None,
        article_count=article_count,
    )


@router.get("/{source_id}/clients-count")
async def get_source_clients_count(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Retourne le nombre et la liste des clients qui utilisent cette source.
    Utilisé pour afficher un avertissement avant suppression.
    """
    result = await db.execute(
        select(Client.id, Client.name)
        .join(ClientMediaSource, ClientMediaSource.client_id == Client.id)
        .where(ClientMediaSource.source_id == source_id)
        .order_by(Client.name)
    )
    rows = result.all()
    return {
        "count": len(rows),
        "clients": [{"id": str(r.id), "name": r.name} for r in rows],
    }


@router.delete("/{source_id}")
async def delete_source(source_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    source = await db.get(MediaSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source introuvable")
    await db.delete(source)
    await db.commit()
    return {"ok": True}


# ── GDELT : découverte de nouvelles sources ────────────────────────────────

@router.get("/flaresolverr/status")
async def flaresolverr_status():
    """Vérifie si FlareSolverr est actif et accessible."""
    from app.services.flaresolverr_service import is_flaresolverr_available
    from app.core.config import settings
    available = await is_flaresolverr_available()
    return {
        "configured": bool(settings.FLARESOLVERR_URL),
        "url": settings.FLARESOLVERR_URL or None,
        "available": available,
    }


@router.get("/discover/gdelt")
async def discover_sources_gdelt(
    query: str = "Maroc OR Morocco OR المغرب",
    db: AsyncSession = Depends(get_db),
):
    """
    Interroge GDELT pour trouver des domaines d'actualité marocains actifs
    non encore référencés dans notre base de sources.
    Retourne une liste triée par fréquence d'apparition sur 24h.
    """
    from app.services.gdelt_service import discover_new_domains
    from urllib.parse import urlparse

    # Charger les domaines existants
    result = await db.execute(select(MediaSource.base_url))
    existing_domains: set[str] = set()
    for (base_url,) in result:
        try:
            domain = urlparse(base_url).netloc.lower().replace("www.", "")
            if domain:
                existing_domains.add(domain)
        except Exception:
            pass

    suggestions = await discover_new_domains(existing_domains, query=query)
    return suggestions

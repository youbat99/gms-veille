"""
System Health API — tableau de bord opérationnel backend.
GET /api/system-health → agrège workers, pipeline, erreurs, sources bloquées, activité récente.
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.models.rss_article import RssArticle
from app.models.article import Article
from app.models.media_source import MediaSource
from app.models.source_crawl_log import SourceCrawlLog

router = APIRouter(prefix="/api/system-health", tags=["system-health"])

# Métadonnées lisibles par job
_JOB_META = {
    "rss_rolling_crawl":  {"label": "Rolling Crawl RSS",        "interval": "10s",  "icon": "rss"},
    "rss_enrich_worker":  {"label": "Enrichissement Trafilatura","interval": "20s",  "icon": "cpu"},
    "rss_match_worker":   {"label": "Matching Keywords",         "interval": "30s",  "icon": "target"},
    "retry_failed_worker":{"label": "Retry Failed",              "interval": "5min", "icon": "rotate"},
    "clustering_worker":  {"label": "Clustering",                "interval": "30min","icon": "layers"},
    "purge_worker":       {"label": "Purge nocturne",            "interval": "03h00","icon": "trash"},
    "gdelt_worker":       {"label": "GDELT Collector",           "interval": "6h",   "icon": "globe"},
    "newsdata_worker":    {"label": "NewsData.io",               "interval": "6h",   "icon": "newspaper"},
}

# Seuils d'alerte par job (secondes depuis le dernier run attendu)
_JOB_STALE_THRESHOLDS = {
    "rss_rolling_crawl":   60,      # doit tourner toutes les 10s → alerte si > 1min
    "rss_enrich_worker":   120,
    "rss_match_worker":    180,
    "retry_failed_worker": 600,
    "clustering_worker":   3600,
    "purge_worker":        90000,   # ~25h (daily)
    "gdelt_worker":        25200,   # 7h
    "newsdata_worker":     25200,
}


@router.get("")
async def get_system_health(db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)

    # ── 1. Workers APScheduler ───────────────────────────────────────────
    from app.main import get_scheduler
    scheduler = get_scheduler()
    workers = []
    if scheduler:
        for job in scheduler.get_jobs():
            jid = job.id
            # Ignorer les slots SerpAPI dynamiques
            if jid.startswith("slot_"):
                continue
            meta = _JOB_META.get(jid, {"label": jid, "interval": "?", "icon": "clock"})
            next_run = job.next_run_time
            # Calculer le dernier run estimé depuis next_run et l'intervalle
            last_run_est = None
            if next_run:
                try:
                    trigger = job.trigger
                    # IntervalTrigger → interval en secondes
                    if hasattr(trigger, "interval"):
                        interval_secs = trigger.interval.total_seconds()
                        last_run_est = next_run - timedelta(seconds=interval_secs)
                    elif hasattr(trigger, "fields"):
                        # CronTrigger → pas d'intervalle simple
                        last_run_est = None
                except Exception:
                    pass

            # Statut : running (next_run dans le passé = en cours), ok, stale
            status = "ok"
            if next_run is None:
                status = "paused"
            elif last_run_est:
                age_secs = (now - last_run_est.astimezone(timezone.utc)).total_seconds()
                threshold = _JOB_STALE_THRESHOLDS.get(jid, 3600)
                if age_secs > threshold * 2:
                    status = "stale"
            workers.append({
                "id": jid,
                "label": meta["label"],
                "interval": meta["interval"],
                "icon": meta["icon"],
                "next_run": next_run.isoformat() if next_run else None,
                "last_run_est": last_run_est.isoformat() if last_run_est else None,
                "status": status,
            })
    # Trier : workers critiques en premier
    order = list(_JOB_META.keys())
    workers.sort(key=lambda w: order.index(w["id"]) if w["id"] in order else 99)

    # ── 2. Funnel RSS pipeline ───────────────────────────────────────────
    status_rows = await db.execute(
        select(RssArticle.status, func.count().label("n"))
        .group_by(RssArticle.status)
    )
    pipeline = {r.status: r.n for r in status_rows}

    # Articles en attente de retry (failed + retry_after <= now)
    retry_ready = await db.scalar(
        select(func.count())
        .where(RssArticle.status == "failed")
        .where(RssArticle.retry_after != None)
        .where(RssArticle.retry_after <= now)
    ) or 0

    # Articles bloqués définitivement (retry_count >= 3 ou retry_after is null)
    blocked = await db.scalar(
        select(func.count())
        .where(RssArticle.status == "failed")
        .where(RssArticle.retry_after == None)
        .where(RssArticle.retry_count >= 3)
    ) or 0

    # Taux d'extraction sur les dernières 24h
    since_24h = now - timedelta(hours=24)
    enriched_24h = await db.scalar(
        select(func.count())
        .where(RssArticle.enriched_at >= since_24h)
        .where(RssArticle.status.in_(["extracted", "matched", "no_match"]))
    ) or 0
    failed_24h = await db.scalar(
        select(func.count())
        .where(RssArticle.enriched_at >= since_24h)
        .where(RssArticle.status == "failed")
    ) or 0
    total_processed_24h = enriched_24h + failed_24h
    success_rate_24h = round(enriched_24h / total_processed_24h * 100) if total_processed_24h > 0 else None

    # ── 3. Erreurs — breakdown par type ─────────────────────────────────
    error_rows = await db.execute(
        select(RssArticle.extraction_error, func.count().label("n"))
        .where(RssArticle.status == "failed")
        .group_by(RssArticle.extraction_error)
        .order_by(func.count().desc())
    )
    errors = [{"type": r.extraction_error or "unknown", "count": r.n} for r in error_rows]

    # Top 5 sources avec le plus d'échecs
    top_failing_rows = await db.execute(
        select(
            MediaSource.name,
            MediaSource.base_url,
            func.count().label("failures")
        )
        .join(RssArticle, RssArticle.source_id == MediaSource.id)
        .where(RssArticle.status == "failed")
        .group_by(MediaSource.id, MediaSource.name, MediaSource.base_url)
        .order_by(func.count().desc())
        .limit(5)
    )
    top_failing_sources = [
        {"name": r.name, "base_url": r.base_url, "failures": r.failures}
        for r in top_failing_rows
    ]

    # ── 4. Sources bloquées (pas crawlées depuis > 2h) ───────────────────
    stale_cutoff = now - timedelta(hours=2)
    stale_rows = await db.execute(
        select(MediaSource.name, MediaSource.base_url, MediaSource.last_crawled_at, MediaSource.crawl_method)
        .where(MediaSource.is_active == True)
        .where(
            (MediaSource.last_crawled_at == None) |
            (MediaSource.last_crawled_at < stale_cutoff)
        )
        .order_by(MediaSource.last_crawled_at.asc().nulls_first())
        .limit(10)
    )
    stalled_sources = [
        {
            "name": r.name,
            "base_url": r.base_url,
            "last_crawled_at": r.last_crawled_at.isoformat() if r.last_crawled_at else None,
            "crawl_method": r.crawl_method,
        }
        for r in stale_rows
    ]

    # ── 5. HITL pipeline (articles → validation humaine) ─────────────────
    hitl_rows = await db.execute(
        select(Article.status, func.count().label("n"))
        .group_by(Article.status)
    )
    hitl = {r.status.value if hasattr(r.status, "value") else str(r.status): r.n for r in hitl_rows}

    weak_signals = await db.scalar(
        select(func.count()).where(Article.weak_signal == True)
        .where(Article.status.in_(["pending", "in_review"]))
    ) or 0

    # ── 6. Activité récente — derniers 25 crawl logs ─────────────────────
    recent_rows = await db.execute(
        select(
            SourceCrawlLog.crawled_at,
            SourceCrawlLog.trigger,
            SourceCrawlLog.new_articles,
            SourceCrawlLog.total_found,
            SourceCrawlLog.duration_ms,
            MediaSource.name.label("source_name"),
            MediaSource.base_url,
        )
        .join(MediaSource, SourceCrawlLog.source_id == MediaSource.id)
        .order_by(SourceCrawlLog.crawled_at.desc())
        .limit(25)
    )
    recent_activity = [
        {
            "crawled_at": r.crawled_at.isoformat(),
            "trigger": r.trigger,
            "new_articles": r.new_articles,
            "total_found": r.total_found,
            "duration_ms": r.duration_ms,
            "source_name": r.source_name,
            "base_url": r.base_url,
        }
        for r in recent_rows
    ]

    # ── 7. Métriques globales sources ────────────────────────────────────
    total_sources = await db.scalar(select(func.count()).where(MediaSource.is_active == True)) or 0
    crawled_1h = await db.scalar(
        select(func.count()).where(MediaSource.is_active == True)
        .where(MediaSource.last_crawled_at >= now - timedelta(hours=1))
    ) or 0

    # Articles collectés / matchés dans les dernières 24h
    collected_24h = await db.scalar(
        select(func.count())
        .where(RssArticle.collected_at >= since_24h)
    ) or 0
    matched_24h = await db.scalar(
        select(func.count())
        .where(RssArticle.collected_at >= since_24h)
        .where(RssArticle.status == "matched")
    ) or 0

    return {
        "generated_at": now.isoformat(),
        "workers": workers,
        "pipeline": {
            "counts": pipeline,
            "retry_ready": retry_ready,
            "blocked_definitif": blocked,
            "success_rate_24h": success_rate_24h,
            "processed_24h": total_processed_24h,
        },
        "errors": {
            "by_type": errors,
            "top_failing_sources": top_failing_sources,
        },
        "sources": {
            "total_active": total_sources,
            "crawled_last_1h": crawled_1h,
            "stalled": stalled_sources,
        },
        "hitl": {
            "by_status": hitl,
            "weak_signals_pending": weak_signals,
        },
        "activity": {
            "recent": recent_activity,
            "collected_24h": collected_24h,
            "matched_24h": matched_24h,
        },
    }

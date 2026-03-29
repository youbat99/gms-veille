import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.api.articles import router as articles_router
from app.api.collector import router as collector_router
from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.api.clients import router as clients_router
from app.api.revues import router as revues_router
from app.api.stats import router as stats_router
from app.api.scheduler import router as scheduler_router
from app.api.collection_logs import router as collection_logs_router
from app.api.media_sources import router as media_sources_router
from app.api.media_feed import router as media_feed_router
from app.api.clusters import router as clusters_router
from app.api.system_health import router as system_health_router
from app.api.newsletter import router as newsletter_router

logger = logging.getLogger(__name__)


async def _run_rss_crawl() -> None:
    """Crawl manuel de toutes les sources (déclenché via API)."""
    from app.core.database import AsyncSessionLocal
    from app.services.rss_service import crawl_all_sources
    try:
        async with AsyncSessionLocal() as db:
            stats = await crawl_all_sources(db)
            logger.info(f"[rss] crawl manuel terminé : {stats}")
    except Exception as e:
        logger.error(f"[rss] erreur crawl manuel : {e}")


# Lock pour éviter les exécutions concurrentes des workers
_crawl_lock = asyncio.Lock()
_crawl_bs_lock = asyncio.Lock()
_crawl_pw_lock = asyncio.Lock()
_enrich_lock = asyncio.Lock()
_match_lock = asyncio.Lock()
_gdelt_lock = asyncio.Lock()
_newsdata_lock = asyncio.Lock()
_retry_lock = asyncio.Lock()
_purge_lock = asyncio.Lock()
_clustering_lock = asyncio.Lock()


def _is_daytime() -> bool:
    """True entre 06h et 22h (heure serveur UTC+1 Maroc)."""
    from datetime import timezone as _tz
    hour = datetime.now(_tz.utc).hour + 1  # UTC+1
    return 6 <= hour < 22


async def _rolling_crawl(
    lock: asyncio.Lock,
    methods: list[str],
    parallel: int,
    label: str,
) -> None:
    """Generic rolling crawl : prend les N sources les plus anciennes parmi les méthodes données,
    en respectant leur crawl_interval_minutes adaptatif."""
    if lock.locked():
        return
    async with lock:
        from app.core.database import AsyncSessionLocal
        from app.services.rss_service import crawl_source_by_id
        from sqlalchemy import select, update as sa_update, text as sa_text
        from app.models.media_source import MediaSource
        from datetime import timedelta

        try:
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(MediaSource.id)
                    .where(
                        MediaSource.is_active == True,
                        MediaSource.crawl_method.in_(methods),
                        # Respecte l'intervalle adaptatif
                        sa_text(
                            "last_crawled_at IS NULL OR "
                            "last_crawled_at < NOW() - (crawl_interval_minutes * interval '1 minute')"
                        ),
                    )
                    .order_by(MediaSource.last_crawled_at.asc().nullsfirst())
                    .limit(parallel)
                )
                source_ids = [row[0] for row in result.all()]
                if not source_ids:
                    return
                await db.execute(
                    sa_update(MediaSource)
                    .where(MediaSource.id.in_(source_ids))
                    .values(last_crawled_at=now)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"[{label}] erreur réservation : {e}")
            return

        async def _one(source_id):
            try:
                async with AsyncSessionLocal() as db:
                    return await crawl_source_by_id(source_id, db)
            except Exception as e:
                logger.error(f"[{label}] erreur source {source_id} : {e}")
                return {}

        results = await asyncio.gather(*[_one(sid) for sid in source_ids])
        for stats in results:
            if stats and stats.get("saved", 0) > 0:
                logger.info(
                    f"[{label}] {stats['source']} → +{stats['saved']} articles "
                    f"({stats.get('duplicates', 0)} dupli.)"
                )


async def _run_rss_rolling_crawl() -> None:
    """RSS + Sitemap — toute la journée, tick 10s, 10 en parallèle."""
    await _rolling_crawl(_crawl_lock, ["rss", "sitemap"], 10, "rss:rolling")


async def _run_bs_rolling_crawl() -> None:
    """BeautifulSoup + FlareSolverr — 06h-22h uniquement, tick 30s, 3 en parallèle."""
    if not _is_daytime():
        return
    await _rolling_crawl(_crawl_bs_lock, ["requests", "flaresolverr"], 3, "bs:rolling")


async def _run_playwright_night_crawl() -> None:
    """Playwright — 1 source à la fois, nuit seulement (03h-06h)."""
    hour = (datetime.now(timezone.utc).hour + 1) % 24  # UTC+1
    if not (3 <= hour < 6):
        return
    await _rolling_crawl(_crawl_pw_lock, ["playwright"], 1, "pw:night")


async def _run_rss_enrich_worker() -> None:
    """Worker d'enrichissement : enrichit un batch d'articles en attente."""
    if _enrich_lock.locked():
        return  # Déjà en cours, on saute ce tick
    async with _enrich_lock:
        from app.core.database import AsyncSessionLocal
        from app.services.rss_service import enrich_pending_batch
        try:
            async with AsyncSessionLocal() as db:
                stats = await enrich_pending_batch(db, batch_size=20)
                if stats.get("enriched", 0) > 0:
                    logger.info(f"[rss:enrich] {stats['enriched']} articles enrichis")
        except Exception as e:
            logger.error(f"[rss:enrich] erreur : {e}")

async def _run_expire_old_pending() -> None:
    """Expire les articles pending de plus de 3 jours — ne seront jamais enrichis."""
    from app.core.database import AsyncSessionLocal
    from app.models.rss_article import RssArticle
    from sqlalchemy import update
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(RssArticle)
                .where(RssArticle.status == "pending")
                .where(RssArticle.collected_at < cutoff)
                .values(
                    status="failed",
                    extraction_error="too_old",
                    retry_after=None,
                )
            )
            await db.commit()
            count = result.rowcount
            if count:
                logger.info(f"[purge:too_old] {count} articles pending expirés (> 3 jours)")
    except Exception as e:
        logger.error(f"[purge:too_old] erreur : {e}")

async def _run_rss_match_worker() -> None:
    """Worker de matching : RSS articles → keywords → articles pending."""
    if _match_lock.locked():
        return
    async with _match_lock:
        from app.core.database import AsyncSessionLocal
        from app.services.rss_matching_service import match_rss_batch
        try:
            async with AsyncSessionLocal() as db:
                stats = await match_rss_batch(db, batch_size=50)
                if stats.get("articles_created", 0) > 0:
                    logger.info(f"[rss:match] {stats['processed']} traités → {stats['articles_created']} articles créés")
        except Exception as e:
            logger.error(f"[rss:match] erreur : {e}")


async def _run_retry_failed_worker() -> None:
    """Retry des articles failed (no_content) avec l'extracteur alternatif."""
    if _retry_lock.locked():
        return
    async with _retry_lock:
        from app.core.database import AsyncSessionLocal
        from app.services.rss_service import retry_failed_batch
        try:
            async with AsyncSessionLocal() as db:
                stats = await retry_failed_batch(db, batch_size=10)
                if stats.get("retried", 0) > 0:
                    logger.info(f"[rss:retry] {stats['retried']} retentés → {stats['recovered']} récupérés")
        except Exception as e:
            logger.error(f"[rss:retry] erreur : {e}")


async def _run_clustering_worker() -> None:
    """Clustering d'articles similaires — toutes les 30min (aujourd'hui + hier)."""
    if _clustering_lock.locked():
        return
    async with _clustering_lock:
        from app.core.database import AsyncSessionLocal
        from app.services.clustering_service import cluster_all_active_revues
        try:
            async with AsyncSessionLocal() as db:
                stats = await cluster_all_active_revues(db)
                if stats["total_clusters"] > 0:
                    logger.info(
                        f"[clustering] {stats['total_clusters']} clusters créés, "
                        f"{stats['total_articles']} articles regroupés "
                        f"({stats['revues']} revues)"
                    )
        except Exception as e:
            logger.error(f"[clustering] erreur : {e}")


async def _run_source_health_check() -> None:
    """
    Teste chaque nuit 31 sources inactives (1860 / 60 jours).
    Si répond → réactivée. Si +60 jours → permanently_dead = True.
    """
    from app.core.database import AsyncSessionLocal
    from app.services.rss_service import detect_rss
    from app.models.media_source import MediaSource
    from sqlalchemy import select, update as sa_update
    from datetime import timedelta

    BATCH = 31
    DEAD_AFTER_DAYS = 60

    try:
        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            # Marquer définitivement mortes celles qui dépassent 60 jours
            await db.execute(
                sa_update(MediaSource)
                .where(
                    MediaSource.is_active == False,
                    MediaSource.permanently_dead == False,
                    MediaSource.deactivated_at <= now - timedelta(days=DEAD_AFTER_DAYS),
                )
                .values(permanently_dead=True)
            )
            await db.commit()

            # Prendre le prochain batch à tester
            result = await db.execute(
                select(MediaSource)
                .where(MediaSource.is_active == False, MediaSource.permanently_dead == False)
                .order_by(MediaSource.deactivated_at.asc().nullsfirst())
                .limit(BATCH)
            )
            sources = result.scalars().all()

        reactivated = 0
        for source in sources:
            try:
                detected = await asyncio.wait_for(detect_rss(source.base_url), timeout=15)
                if detected and detected.get("rss_type") != "playwright":
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            sa_update(MediaSource)
                            .where(MediaSource.id == source.id)
                            .values(
                                is_active=True,
                                deactivated_at=None,
                                permanently_dead=False,
                                rss_url=detected["rss_url"],
                                rss_type=detected["rss_type"],
                                crawl_method=detected["crawl_method"],
                            )
                        )
                        await db.commit()
                    reactivated += 1
                    logger.info(f"[health_check] ✅ réactivée : {source.name} ({detected['crawl_method']})")
            except Exception:
                pass

        if reactivated or sources:
            logger.info(f"[health_check] {reactivated}/{len(sources)} sources réactivées cette nuit")

    except Exception as e:
        logger.error(f"[health_check] erreur : {e}")


async def _run_purge_worker() -> None:
    """Purge des articles no_match et failed définitifs (+30 jours)."""
    if _purge_lock.locked():
        return
    async with _purge_lock:
        from app.core.database import AsyncSessionLocal
        from app.services.rss_service import purge_old_articles
        try:
            async with AsyncSessionLocal() as db:
                stats = await purge_old_articles(db, days=30)
                if stats["no_match_deleted"] + stats["failed_deleted"] > 0:
                    logger.info(f"[purge] {stats['no_match_deleted']} no_match + {stats['failed_deleted']} failed supprimés")
        except Exception as e:
            logger.error(f"[purge] erreur : {e}")


async def _run_gdelt_worker() -> None:
    """
    Worker GDELT (toutes les 6h) :
    - Pour chaque keyword actif → requête GDELT → articles depuis sources connues
    - Insert dans RssArticle si non-dupliqués (url_hash + near-dedup)
    """
    if _gdelt_lock.locked():
        return
    async with _gdelt_lock:
        from app.core.database import AsyncSessionLocal
        from app.services.gdelt_service import fetch_articles_for_keyword
        from app.services.dedup_service import compute_fingerprint, is_near_duplicate
        from app.models.revue import Keyword
        from app.models.media_source import MediaSource
        from app.models.rss_article import RssArticle
        from sqlalchemy import select
        from datetime import datetime

        try:
            async with AsyncSessionLocal() as db:
                # 1. Charger tous les keywords actifs
                kw_result = await db.execute(
                    select(Keyword).where(Keyword.is_active == True)
                )
                keywords = kw_result.scalars().all()
                if not keywords:
                    return

                # 2. Charger tous les domaines connus (base_url → domain)
                src_result = await db.execute(
                    select(MediaSource.id, MediaSource.base_url)
                    .where(MediaSource.is_active == True)
                )
                domain_to_source_id: dict[str, str] = {}
                for src_id, base_url in src_result:
                    try:
                        from urllib.parse import urlparse
                        domain = urlparse(base_url).netloc.lower().replace("www.", "")
                        if domain:
                            domain_to_source_id[domain] = str(src_id)
                    except Exception:
                        pass

                known_domains = set(domain_to_source_id.keys())
                if not known_domains:
                    return

                total_saved = 0
                total_dupes = 0

                for kw in keywords:
                    articles = await fetch_articles_for_keyword(
                        kw.term, known_domains, hours=6
                    )
                    for art in articles:
                        source_id = domain_to_source_id.get(art["source_domain"])
                        if not source_id:
                            continue

                        # Dédup exact (URL)
                        existing = await db.execute(
                            select(RssArticle.id).where(RssArticle.url_hash == art["url_hash"])
                        )
                        if existing.scalar_one_or_none():
                            total_dupes += 1
                            continue

                        # Near-dedup (titre SimHash vs 100 derniers articles de la source)
                        fp = compute_fingerprint(art["title"])
                        fp_result = await db.execute(
                            select(RssArticle.content_fingerprint)
                            .where(
                                RssArticle.source_id == source_id,
                                RssArticle.content_fingerprint.is_not(None),
                            )
                            .order_by(RssArticle.collected_at.desc())
                            .limit(100)
                        )
                        recent_fps = [r[0] for r in fp_result if r[0]]
                        if any(is_near_duplicate(fp, efp) for efp in recent_fps):
                            total_dupes += 1
                            continue

                        import uuid
                        db.add(RssArticle(
                            id=uuid.uuid4(),
                            source_id=source_id,
                            url=art["url"],
                            url_hash=art["url_hash"],
                            title=art["title"],
                            image_url=art.get("image_url"),
                            published_at=art.get("published_at"),
                            detected_language=art.get("detected_language"),
                            content_fingerprint=fp,
                            # enriched_at = None → passera dans le worker Trafilatura
                        ))
                        total_saved += 1

                if total_saved > 0 or total_dupes > 0:
                    await db.commit()
                    logger.info(f"[gdelt] +{total_saved} articles, {total_dupes} dupes ignorés")

        except Exception as e:
            logger.error(f"[gdelt] erreur worker : {e}")


async def _run_newsdata_worker() -> None:
    """
    Worker NewsData.io (toutes les 6h) :
    - Collecte générale AR+FR Maroc + collecte par keyword actifs
    - Insert dans RssArticle si non-dupliqués
    """
    if _newsdata_lock.locked():
        return
    async with _newsdata_lock:
        from app.core.config import settings
        if not settings.NEWSDATA_API_KEY:
            return

        from app.core.database import AsyncSessionLocal
        from app.services.newsdata_service import fetch_all_morocco_articles, fetch_articles_for_keyword
        from app.services.dedup_service import compute_fingerprint, is_near_duplicate
        from app.models.revue import Keyword
        from app.models.media_source import MediaSource
        from app.models.rss_article import RssArticle
        from sqlalchemy import select
        import uuid

        try:
            async with AsyncSessionLocal() as db:
                # Charger domaines connus
                src_result = await db.execute(
                    select(MediaSource.id, MediaSource.base_url)
                    .where(MediaSource.is_active == True)
                )
                domain_to_source_id: dict[str, str] = {}
                for src_id, base_url in src_result:
                    try:
                        from urllib.parse import urlparse
                        domain = urlparse(base_url).netloc.lower().replace("www.", "")
                        if domain:
                            domain_to_source_id[domain] = str(src_id)
                    except Exception:
                        pass

                known_domains = set(domain_to_source_id.keys())
                if not known_domains:
                    return

                # Collecte générale + par keywords actifs
                all_arts = await fetch_all_morocco_articles(settings.NEWSDATA_API_KEY, known_domains)

                kw_result = await db.execute(select(Keyword).where(Keyword.is_active == True))
                for kw in kw_result.scalars().all():
                    kw_arts = await fetch_articles_for_keyword(settings.NEWSDATA_API_KEY, kw.term, known_domains)
                    all_arts.extend(kw_arts)

                total_saved = 0
                seen_hashes: set[str] = set()

                for art in all_arts:
                    if art["url_hash"] in seen_hashes:
                        continue
                    seen_hashes.add(art["url_hash"])

                    source_id = domain_to_source_id.get(art["source_domain"])
                    if not source_id:
                        continue

                    # Dédup exact
                    existing = await db.execute(
                        select(RssArticle.id).where(RssArticle.url_hash == art["url_hash"])
                    )
                    if existing.scalar_one_or_none():
                        continue

                    # Near-dedup
                    fp = compute_fingerprint(art["title"])
                    fp_result = await db.execute(
                        select(RssArticle.content_fingerprint)
                        .where(
                            RssArticle.source_id == source_id,
                            RssArticle.content_fingerprint.is_not(None),
                        )
                        .order_by(RssArticle.collected_at.desc())
                        .limit(100)
                    )
                    recent_fps = [r[0] for r in fp_result if r[0]]
                    if any(is_near_duplicate(fp, efp) for efp in recent_fps):
                        continue

                    db.add(RssArticle(
                        id=uuid.uuid4(),
                        source_id=source_id,
                        url=art["url"],
                        url_hash=art["url_hash"],
                        title=art["title"],
                        summary=art.get("summary"),
                        image_url=art.get("image_url"),
                        published_at=art.get("published_at"),
                        detected_language=art.get("detected_language"),
                        content_fingerprint=fp,
                    ))
                    total_saved += 1

                if total_saved > 0:
                    await db.commit()
                    logger.info(f"[newsdata] +{total_saved} articles insérés")

        except Exception as e:
            logger.error(f"[newsdata] erreur worker : {e}")


TZ = "Africa/Casablanca"

# Scheduler global — accessible depuis l'API pour rechargement dynamique
_scheduler: AsyncIOScheduler | None = None


# ── Collecte d'une revue spécifique avec paramètres SerpAPI ────────────
async def _run_revue_slot(
    revue_id: str,
    tbs: str,
    language: str,
    num_results: int,
    engine: str = "google_news",
    gl: str = "ma",
    sort_by: str = "date",
    safe_search: bool = True,
    keyword_ids: list[str] | None = None,
) -> None:
    """Exécute la collecte planifiée pour une revue avec ses paramètres SerpAPI."""
    import uuid as uuid_mod
    from app.core.database import AsyncSessionLocal
    from app.services.collector_service import collector_service

    kw_ids = [uuid_mod.UUID(k) for k in keyword_ids] if keyword_ids else None

    try:
        async with AsyncSessionLocal() as db:
            res = await collector_service.collect_for_revue(
                revue_id=uuid_mod.UUID(revue_id),
                db=db,
                tbs=tbs,
                num_results=num_results,
                engine=engine,
                gl=gl,
                sort_by=sort_by,
                safe_search=safe_search,
                language_override=language,
                trigger="scheduled",
                keyword_ids=kw_ids,
            )
            logger.info(f"[scheduler] revue={revue_id} tbs={tbs} engine={engine} gl={gl} → {res}")
    except Exception as e:
        logger.error(f"[scheduler] revue={revue_id} error: {e}")


# ── Rechargement dynamique des jobs ───────────────────────────────────
def get_scheduler() -> AsyncIOScheduler | None:
    """Retourne le scheduler global — utilisé par l'API system-health."""
    return _scheduler


async def _run_newsletter_job(revue_id: str) -> None:
    """Envoie la revue de presse planifiée pour une revue."""
    from app.core.database import AsyncSessionLocal
    from app.services.email_service import send_newsletter
    import uuid as uuid_mod
    try:
        async with AsyncSessionLocal() as db:
            result = await send_newsletter(db, uuid_mod.UUID(revue_id), triggered_by="scheduled")
            if result["status"] == "sent":
                logger.info(f"[newsletter] envoyé revue={revue_id} → {result['recipients']} ({result['article_count']} articles)")
            else:
                logger.error(f"[newsletter] erreur revue={revue_id}: {result.get('error')}")
    except Exception as e:
        logger.error(f"[newsletter] erreur job revue={revue_id}: {e}")


async def reload_newsletter_jobs() -> None:
    """Recharge les jobs newsletter depuis la DB (appelé après update config)."""
    global _scheduler
    if _scheduler is None:
        return
    from app.core.database import AsyncSessionLocal
    from app.models.newsletter import NewsletterConfig
    from sqlalchemy import select as sa_select

    # Supprimer les anciens jobs newsletter
    for job in _scheduler.get_jobs():
        if job.id.startswith("newsletter_"):
            _scheduler.remove_job(job.id)

    try:
        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                sa_select(NewsletterConfig).where(NewsletterConfig.enabled == True)
            )
            configs = rows.scalars().all()

        for cfg in configs:
            job_id = f"newsletter_{cfg.revue_id}"
            _scheduler.add_job(
                _run_newsletter_job,
                CronTrigger(hour=cfg.schedule_hour, minute=cfg.schedule_minute, timezone=TZ),
                id=job_id,
                replace_existing=True,
                kwargs={"revue_id": str(cfg.revue_id)},
            )
            logger.info(f"[newsletter] job {job_id} → {cfg.schedule_hour:02d}:{cfg.schedule_minute:02d}")
    except Exception as e:
        logger.error(f"[newsletter] erreur rechargement jobs: {e}")


def reload_scheduler_jobs(slots: list[dict]) -> None:
    """
    Recharge les jobs du scheduler depuis une liste de dicts :
      id, revue_id, hour, minute, tbs, language, num_results,
      engine, gl, sort_by, safe_search
    Appelé depuis l'API quand les créneaux changent.
    """
    global _scheduler
    if _scheduler is None:
        return

    # Supprimer tous les jobs de collecte existants
    for job in _scheduler.get_jobs():
        if job.id.startswith("slot_"):
            _scheduler.remove_job(job.id)

    # Re-ajouter depuis la nouvelle liste
    for slot in slots:
        job_id = f"slot_{slot['id']}"
        _scheduler.add_job(
            _run_revue_slot,
            CronTrigger(hour=slot["hour"], minute=slot["minute"], timezone=TZ),
            id=job_id,
            replace_existing=True,
            kwargs={
                "revue_id":    slot["revue_id"],
                "tbs":         slot["tbs"],
                "language":    slot["language"],
                "num_results": slot["num_results"],
                "engine":      slot.get("engine",      "google_news"),
                "gl":          slot.get("gl",           "ma"),
                "sort_by":     slot.get("sort_by",      "date"),
                "safe_search": slot.get("safe_search",  True),
                "keyword_ids": slot.get("keyword_ids") or None,
            },
        )

    labels = [f"{s['hour']:02d}:{s['minute']:02d}(rev:{s['revue_id'][:8]})" for s in slots]
    logger.info(f"[scheduler] rechargé : {labels or 'aucun créneau'}")


# ── Lifespan ───────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=TZ)

    # Charger les créneaux depuis la DB
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.scheduler import SchedulerSlot

        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                select(SchedulerSlot)
                .where(SchedulerSlot.enabled == True)
                .order_by(SchedulerSlot.hour, SchedulerSlot.minute)
            )
            slots = rows.scalars().all()

        for slot in slots:
            job_id = f"slot_{slot.id}"
            _scheduler.add_job(
                _run_revue_slot,
                CronTrigger(hour=slot.hour, minute=slot.minute, timezone=TZ),
                id=job_id,
                kwargs={
                    "revue_id":    str(slot.revue_id),
                    "tbs":         slot.tbs,
                    "language":    slot.language,
                    "num_results": slot.num_results,
                    "engine":      slot.engine,
                    "gl":          slot.gl,
                    "sort_by":     slot.sort_by,
                    "safe_search": slot.safe_search,
                    "keyword_ids": [str(sk.keyword_id) for sk in slot.slot_keywords] or None,
                },
            )
            logger.info(
                f"[scheduler] créneau : {slot.hour:02d}:{slot.minute:02d} "
                f"revue={slot.revue_id} tbs={slot.tbs} engine={slot.engine} gl={slot.gl}"
            )

    except Exception as e:
        logger.error(f"[scheduler] erreur chargement créneaux: {e}")

    # ── Rolling crawl RSS/Sitemap — toute la journée, tick 10s ────────
    _scheduler.add_job(
        _run_rss_rolling_crawl,
        IntervalTrigger(seconds=10, timezone=TZ),
        id="rss_rolling_crawl",
        replace_existing=True,
        max_instances=1,
    )

    # ── Rolling crawl BS/FlareSolverr — 06h-22h, tick 30s ─────────────
    _scheduler.add_job(
        _run_bs_rolling_crawl,
        IntervalTrigger(seconds=30, timezone=TZ),
        id="bs_rolling_crawl",
        replace_existing=True,
        max_instances=1,
    )

    # ── Playwright nocturne — 03h-06h, tick 60s, 1 à la fois ──────────
    _scheduler.add_job(
        _run_playwright_night_crawl,
        IntervalTrigger(seconds=60, timezone=TZ),
        id="playwright_night_crawl",
        replace_existing=True,
        max_instances=1,
    )

    # Worker d'enrichissement — batch 20 articles toutes les 10s (Traf+Jina)
    _scheduler.add_job(
        _run_rss_enrich_worker,
        IntervalTrigger(seconds=10, timezone=TZ),
        id="rss_enrich_worker",
        replace_existing=True,
        max_instances=1,
    )

    # Purge articles pending > 3 jours — toutes les heures
    _scheduler.add_job(
        _run_expire_old_pending,
        IntervalTrigger(hours=1, timezone=TZ),
        id="expire_old_pending",
        replace_existing=True,
        max_instances=1,
    )

    # Worker de matching RSS → Keywords → Articles (pending validation)
    # Tourne toutes les 30s — après enrichissement pour avoir image_url + content
    _scheduler.add_job(
        _run_rss_match_worker,
        IntervalTrigger(seconds=30, timezone=TZ),
        id="rss_match_worker",
        replace_existing=True,
        max_instances=1,
    )

    # Retry des articles failed — toutes les 5min (retry_after contrôle le délai réel)
    _scheduler.add_job(
        _run_retry_failed_worker,
        IntervalTrigger(minutes=5, timezone=TZ),
        id="retry_failed_worker",
        replace_existing=True,
        max_instances=1,
    )

    # Clustering désactivé
    # _scheduler.add_job(
    #     _run_clustering_worker,
    #     IntervalTrigger(minutes=30, timezone=TZ),
    #     id="clustering_worker",
    #     replace_existing=True,
    #     max_instances=1,
    # )

    # Purge quotidienne — supprime no_match et failed définitifs (+30 jours)
    _scheduler.add_job(
        _run_purge_worker,
        CronTrigger(hour=3, minute=0, timezone=TZ),
        id="purge_worker",
        replace_existing=True,
        max_instances=1,
    )

    # Health check noctune — teste 31 sources inactives/nuit (cycle 60 jours)
    _scheduler.add_job(
        _run_source_health_check,
        CronTrigger(hour=4, minute=30, timezone=TZ),
        id="source_health_check",
        replace_existing=True,
        max_instances=1,
    )

    # Worker GDELT — collecte d'articles depuis sources connues via keywords actifs
    # Tourne toutes les 6h (GDELT refresh = 15min, rate limit non documenté)
    _scheduler.add_job(
        _run_gdelt_worker,
        IntervalTrigger(hours=6, timezone=TZ),
        id="gdelt_worker",
        replace_existing=True,
        max_instances=1,
    )

    # Worker NewsData.io — collecte Maroc AR+FR (si clé API configurée)
    # Tourne toutes les 6h : 4 runs/jour × ~30 req = ~120 req/jour (< 200 limite free)
    from app.core.config import settings
    if settings.NEWSDATA_API_KEY:
        _scheduler.add_job(
            _run_newsdata_worker,
            IntervalTrigger(hours=6, timezone=TZ),
            id="newsdata_worker",
            replace_existing=True,
            max_instances=1,
        )
        logger.info("[newsdata] worker activé (clé API configurée)")
    else:
        logger.info("[newsdata] worker désactivé (NEWSDATA_API_KEY non configurée)")

    # ── Newsletter jobs ────────────────────────────────────────────────
    try:
        from app.models.newsletter import NewsletterConfig
        from sqlalchemy import select as _sel
        async with AsyncSessionLocal() as db:
            rows = await db.execute(_sel(NewsletterConfig).where(NewsletterConfig.enabled == True))
            nl_configs = rows.scalars().all()
        for cfg in nl_configs:
            job_id = f"newsletter_{cfg.revue_id}"
            _scheduler.add_job(
                _run_newsletter_job,
                CronTrigger(hour=cfg.schedule_hour, minute=cfg.schedule_minute, timezone=TZ),
                id=job_id,
                replace_existing=True,
                kwargs={"revue_id": str(cfg.revue_id)},
            )
            logger.info(f"[newsletter] créneau : {cfg.schedule_hour:02d}:{cfg.schedule_minute:02d} revue={cfg.revue_id}")
    except Exception as e:
        logger.error(f"[newsletter] erreur chargement jobs: {e}")

    _scheduler.start()
    logger.info(f"[scheduler] démarré avec {len(_scheduler.get_jobs())} créneaux")
    yield
    _scheduler.shutdown()
    logger.info("[scheduler] arrêté")


# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(title="Veille Média Maroc API", version="0.1.0", lifespan=lifespan)

from app.core.config import settings as _settings

# Origines autorisées : localhost en dev + FRONTEND_URL en prod
_allowed_origins = list({
    "http://localhost:3000",
    "http://localhost:3001",
    _settings.FRONTEND_URL,
    _settings.APP_URL,
})

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(clients_router)
app.include_router(revues_router)
app.include_router(articles_router)
app.include_router(collector_router)
app.include_router(stats_router)
app.include_router(scheduler_router)
app.include_router(collection_logs_router)
app.include_router(media_sources_router)
app.include_router(media_feed_router)
app.include_router(clusters_router)
app.include_router(system_health_router)
app.include_router(newsletter_router)


@app.get("/health")
async def health():
    return {"status": "ok"}

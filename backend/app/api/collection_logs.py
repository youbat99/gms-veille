"""
API pour consulter l'historique des collectes de scraping.
"""
import uuid
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_admin_plus
from app.models.collection_log import CollectionLog

router = APIRouter(prefix="/collection-logs", tags=["collection-logs"])


class LogOut(BaseModel):
    id: str
    revue_id: str | None
    revue_name: str
    triggered_at: datetime
    finished_at: datetime | None
    trigger: str
    tbs: str | None
    collected: int
    errors: int
    duplicates: int
    filtered_old: int
    status: str
    duration_ms: int | None
    # Params SerpAPI
    engine: str | None
    gl: str | None
    language: str | None
    sort_by: str | None
    as_qdr: str | None
    safe_search: bool | None
    num_results: int | None

    class Config:
        from_attributes = True


@router.get("/", response_model=List[LogOut])
async def list_logs(
    revue_id: Optional[str] = Query(None, description="Filtrer par revue"),
    status: Optional[str] = Query(None, description="success | partial | error"),
    trigger: Optional[str] = Query(None, description="manual | scheduled"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin_plus),
):
    """Retourne l'historique des collectes, du plus récent au plus ancien."""
    q = (
        select(CollectionLog)
        .order_by(desc(CollectionLog.triggered_at))
        .limit(limit)
        .offset(offset)
    )
    if revue_id:
        q = q.where(CollectionLog.revue_id == uuid.UUID(revue_id))
    if status:
        q = q.where(CollectionLog.status == status)
    if trigger:
        q = q.where(CollectionLog.trigger == trigger)

    rows = await db.execute(q)
    return [_log_out(l) for l in rows.scalars()]


@router.get("/stats")
async def log_stats(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin_plus),
):
    """Résumé rapide : total, succès, erreurs, articles collectés."""
    from sqlalchemy import func, case
    q = select(
        func.count().label("total"),
        func.sum(case((CollectionLog.status == "success", 1), else_=0)).label("success"),
        func.sum(case((CollectionLog.status == "error", 1), else_=0)).label("errors"),
        func.sum(CollectionLog.collected).label("total_collected"),
        func.sum(CollectionLog.errors).label("total_errors"),
    )
    result = await db.execute(q)
    row = result.one()
    return {
        "total_runs":       row.total or 0,
        "success_runs":     row.success or 0,
        "error_runs":       row.errors or 0,
        "total_collected":  row.total_collected or 0,
        "total_errors":     row.total_errors or 0,
    }


@router.get("/{log_id}/details")
async def log_details(
    log_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin_plus),
):
    """Retourne le détail complet d'une collecte : params SerpAPI + articles trouvés."""
    log = await db.get(CollectionLog, uuid.UUID(log_id))
    if not log:
        from fastapi import HTTPException
        raise HTTPException(404, "Log introuvable")
    return {
        **_log_out(log).model_dump(),
        "articles_found": log.articles_found or [],
    }


# ── Helper ────────────────────────────────────────────────────────────
def _log_out(l: CollectionLog) -> LogOut:
    return LogOut(
        id=str(l.id),
        revue_id=str(l.revue_id) if l.revue_id else None,
        revue_name=l.revue_name,
        triggered_at=l.triggered_at,
        finished_at=l.finished_at,
        trigger=l.trigger,
        tbs=l.tbs,
        collected=l.collected,
        errors=l.errors,
        duplicates=l.duplicates,
        filtered_old=getattr(l, "filtered_old", 0) or 0,
        status=l.status,
        duration_ms=l.duration_ms,
        engine=getattr(l, "engine", None),
        gl=getattr(l, "gl", None),
        language=getattr(l, "language", None),
        sort_by=getattr(l, "sort_by", None),
        as_qdr=getattr(l, "as_qdr", None),
        safe_search=getattr(l, "safe_search", None),
        num_results=getattr(l, "num_results", None),
    )

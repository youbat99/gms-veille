"""
API CRUD pour les créneaux de collecte automatique.
Chaque créneau contient tous les paramètres SerpAPI configurables.
"""
import uuid
from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_admin_plus as require_gms_admin
from app.models.scheduler import SchedulerSlot, SchedulerSlotKeyword
from app.services.serpapi_service import GL_OPTIONS, ENGINE_OPTIONS, SORT_OPTIONS

router = APIRouter(prefix="/scheduler", tags=["scheduler"])

TBS_OPTIONS = {
    "qdr:h":   "Dernière heure (1h)",
    "qdr:h2":  "2 dernières heures",
    "qdr:h3":  "3 dernières heures",
    "qdr:h4":  "4 dernières heures",
    "qdr:h6":  "6 dernières heures",
    "qdr:h8":  "8 dernières heures",
    "qdr:h12": "12 dernières heures",
    "qdr:d":   "Dernier jour (24h)",
    "qdr:w":   "Dernière semaine",
    "qdr:m":   "Dernier mois",
}


# ── Schemas ──────────────────────────────────────────────────────────────────
class SlotOut(BaseModel):
    id:          str
    revue_id:    str
    revue_name:  str
    hour:        int
    minute:      int
    label:       str
    enabled:     bool
    tbs:         str
    language:    str
    num_results: int
    engine:      str
    gl:          str
    sort_by:     str
    safe_search: bool
    keyword_ids: list[str] = []

    class Config:
        from_attributes = True


class SlotCreate(BaseModel):
    revue_id:    str
    hour:        int  = Field(...,            ge=0, le=23)
    minute:      int  = Field(0,              ge=0, le=59)
    label:       str  = Field("",            max_length=64)
    enabled:     bool = True
    tbs:         str  = Field("qdr:d",       max_length=32)
    language:    str  = Field("fr",          max_length=8)
    num_results: int  = Field(100,            ge=1, le=100)
    engine:      str  = Field("google_news", max_length=32)
    gl:          str  = Field("ma",          max_length=8)
    sort_by:     str  = Field("date",        max_length=16)
    safe_search: bool = True
    keyword_ids: list[str] = []


class SlotUpdate(BaseModel):
    hour:        int  | None = Field(None, ge=0, le=23)
    minute:      int  | None = Field(None, ge=0, le=59)
    label:       str  | None = None
    enabled:     bool | None = None
    tbs:         str  | None = None
    language:    str  | None = None
    num_results: int  | None = Field(None, ge=1, le=100)
    engine:      str  | None = None
    gl:          str  | None = None
    sort_by:     str  | None = None
    safe_search: bool | None = None
    keyword_ids: list[str] | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("/slots", response_model=List[SlotOut])
async def list_slots(
    revue_id: Optional[str] = Query(None, description="Filtrer par revue"),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_gms_admin),
):
    from app.models.revue import Revue
    q = (
        select(SchedulerSlot, Revue.name.label("revue_name"))
        .join(Revue, Revue.id == SchedulerSlot.revue_id)
        .order_by(SchedulerSlot.hour, SchedulerSlot.minute)
    )
    if revue_id:
        q = q.where(SchedulerSlot.revue_id == uuid.UUID(revue_id))
    rows = await db.execute(q)
    return [_slot_out(s, rname) for s, rname in rows.all()]


@router.post("/slots", response_model=SlotOut, status_code=201)
async def create_slot(
    body: SlotCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_gms_admin),
):
    from app.models.revue import Revue
    revue = await db.get(Revue, uuid.UUID(body.revue_id))
    if not revue:
        raise HTTPException(404, "Revue introuvable")

    slot = SchedulerSlot(
        revue_id=uuid.UUID(body.revue_id),
        hour=body.hour, minute=body.minute, label=body.label, enabled=body.enabled,
        tbs=body.tbs, language=body.language, num_results=body.num_results,
        engine=body.engine, gl=body.gl, sort_by=body.sort_by, safe_search=body.safe_search,
    )
    db.add(slot)
    await db.flush()  # slot.id is now available

    # Associate keywords
    for kid in body.keyword_ids:
        db.add(SchedulerSlotKeyword(slot_id=slot.id, keyword_id=uuid.UUID(kid)))

    await db.commit()
    await db.refresh(slot)
    await _reload_scheduler(db)
    return _slot_out(slot, revue.name)


@router.patch("/slots/{slot_id}", response_model=SlotOut)
async def update_slot(
    slot_id: uuid.UUID,
    body: SlotUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_gms_admin),
):
    from app.models.revue import Revue
    slot = await db.get(SchedulerSlot, slot_id)
    if not slot:
        raise HTTPException(404, "Créneau introuvable")

    update_data = body.model_dump(exclude_none=True, exclude={"keyword_ids"})
    for field_name, val in update_data.items():
        setattr(slot, field_name, val)

    # Update keyword associations if provided
    if body.keyword_ids is not None:
        await db.execute(
            delete(SchedulerSlotKeyword).where(SchedulerSlotKeyword.slot_id == slot_id)
        )
        for kid in body.keyword_ids:
            db.add(SchedulerSlotKeyword(slot_id=slot_id, keyword_id=uuid.UUID(kid)))

    await db.commit()
    await db.refresh(slot)
    await _reload_scheduler(db)

    revue = await db.get(Revue, slot.revue_id)
    return _slot_out(slot, revue.name if revue else "")


@router.delete("/slots/{slot_id}", status_code=204)
async def delete_slot(
    slot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_gms_admin),
):
    slot = await db.get(SchedulerSlot, slot_id)
    if not slot:
        raise HTTPException(404, "Créneau introuvable")
    await db.delete(slot)
    await db.commit()
    await _reload_scheduler(db)


@router.post("/slots/{slot_id}/run-now", status_code=202)
async def run_slot_now(
    slot_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_gms_admin),
):
    """Lance immédiatement ce créneau avec ses propres paramètres et mots-clés."""
    from app.models.revue import Revue
    from app.services.collector_service import collector_service
    from app.core.database import AsyncSessionLocal

    slot = await db.get(SchedulerSlot, slot_id)
    if not slot:
        raise HTTPException(404, "Créneau introuvable")

    revue = await db.get(Revue, slot.revue_id)

    # Snapshot slot params (slot may mutate between request and background task)
    revue_id    = slot.revue_id
    tbs         = slot.tbs
    language    = slot.language
    num_results = slot.num_results
    engine      = slot.engine
    gl          = slot.gl
    sort_by     = slot.sort_by
    safe_search = slot.safe_search
    keyword_ids = [sk.keyword_id for sk in slot.slot_keywords] or None

    async def _run():
        async with AsyncSessionLocal() as s:
            await collector_service.collect_for_revue(
                revue_id=revue_id, db=s,
                tbs=tbs, num_results=num_results,
                engine=engine, gl=gl,
                sort_by=sort_by, safe_search=safe_search,
                language_override=language or None,
                trigger="manual",
                keyword_ids=keyword_ids,
            )

    background_tasks.add_task(_run)
    return {"status": "started", "slot_id": str(slot_id), "revue": revue.name if revue else ""}


@router.post("/run-now", status_code=202)
async def run_now(
    background_tasks: BackgroundTasks,
    revue_id:    Optional[str] = Query(None),
    tbs:         str  = Query(default="qdr:d"),
    engine:      str  = Query(default="google_news"),
    gl:          str  = Query(default="ma"),
    language:    str  = Query(default=""),
    num_results: int  = Query(default=100, ge=1, le=100),
    sort_by:     str  = Query(default="date"),
    safe_search: bool = Query(default=True),
    keyword_ids: str  = Query(default=""),  # UUIDs séparés par des virgules
    db: AsyncSession = Depends(get_db),
    _=Depends(require_gms_admin),
):
    """Lance la collecte avec les paramètres SerpAPI choisis. Retourne 202 immédiatement."""
    from app.models.revue import Revue
    from app.services.collector_service import collector_service
    from app.core.database import AsyncSessionLocal

    if revue_id:
        revue_ids = [uuid.UUID(revue_id)]
    else:
        rows = await db.execute(select(Revue.id).where(Revue.is_active == True))
        revue_ids = list(rows.scalars().all())

    kw_ids = [uuid.UUID(k.strip()) for k in keyword_ids.split(",") if k.strip()] or None

    async def _run():
        for rid in revue_ids:
            async with AsyncSessionLocal() as s:
                await collector_service.collect_for_revue(
                    revue_id=rid, db=s,
                    tbs=tbs, num_results=num_results,
                    engine=engine, gl=gl,
                    sort_by=sort_by, safe_search=safe_search,
                    language_override=language or None,
                    trigger="manual",
                    keyword_ids=kw_ids,
                )

    background_tasks.add_task(_run)
    return {"status": "started", "revues": len(revue_ids)}


@router.get("/options")
async def serp_options(_=Depends(require_gms_admin)):
    """Toutes les options disponibles pour les formulaires admin."""
    return {
        "tbs":    [{"value": k, "label": v} for k, v in TBS_OPTIONS.items()],
        "gl":     GL_OPTIONS,
        "engine": ENGINE_OPTIONS,
        "sort":   SORT_OPTIONS,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────
def _slot_out(s: SchedulerSlot, revue_name: str) -> SlotOut:
    return SlotOut(
        id=str(s.id), revue_id=str(s.revue_id), revue_name=revue_name,
        hour=s.hour, minute=s.minute, label=s.label, enabled=s.enabled,
        tbs=s.tbs, language=s.language, num_results=s.num_results,
        engine=s.engine, gl=s.gl, sort_by=s.sort_by, safe_search=s.safe_search,
        keyword_ids=[str(sk.keyword_id) for sk in s.slot_keywords],
    )


async def _reload_scheduler(db: AsyncSession):
    from app.main import reload_scheduler_jobs
    rows = await db.execute(select(SchedulerSlot).where(SchedulerSlot.enabled == True))
    slots = rows.scalars().all()
    reload_scheduler_jobs([{
        "id": str(s.id), "revue_id": str(s.revue_id),
        "hour": s.hour, "minute": s.minute,
        "tbs": s.tbs, "language": s.language, "num_results": s.num_results,
        "engine": s.engine, "gl": s.gl, "sort_by": s.sort_by, "safe_search": s.safe_search,
        "keyword_ids": [str(sk.keyword_id) for sk in s.slot_keywords] or None,
    } for s in slots])

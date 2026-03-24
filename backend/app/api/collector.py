import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.collector_service import collector_service

router = APIRouter(prefix="/collector", tags=["collector"])


@router.post("/revue/{revue_id}/collect")
async def trigger_collection(
    revue_id: uuid.UUID,
    tbs: Optional[str] = Query(None, description="Filtre temporel SerpAPI (ex: qdr:h, qdr:d, qdr:w). Auto si absent."),
    db: AsyncSession = Depends(get_db),
):
    """Lance manuellement la collecte pour une revue.
    Le filtre temps (tbs) est calculé automatiquement selon l'intervalle du keyword si non fourni.
    """
    result = await collector_service.collect_for_revue(revue_id=revue_id, db=db, tbs=tbs)
    return {
        "revue_id": str(revue_id),
        "collected": result["collected"],
        "errors": result["errors"],
        "duplicates": result["duplicates"],
    }

"""
API Newsletter — envoi de la revue de presse par email.
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_admin_plus, require_any_role
from app.models.client import Account
from app.models.newsletter import NewsletterConfig, EmailLog
from app.models.revue import Revue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/newsletter", tags=["newsletter"])


# ── Schémas ──────────────────────────────────────────────────────────────

class NewsletterConfigOut(BaseModel):
    revue_id: str
    enabled: bool
    schedule_hour: int
    schedule_minute: int
    extra_recipients: list[str]
    include_client_email: bool
    subject_template: str
    last_sent_at: Optional[datetime]

    model_config = {"from_attributes": True}


class NewsletterConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    schedule_hour: Optional[int] = None
    schedule_minute: Optional[int] = None
    extra_recipients: Optional[list[str]] = None
    include_client_email: Optional[bool] = None
    subject_template: Optional[str] = None


class EmailLogOut(BaseModel):
    id: str
    revue_id: str
    sent_at: datetime
    recipients: list[str]
    article_count: int
    period_from: Optional[datetime]
    period_to: Optional[datetime]
    subject: str
    status: str
    error_message: Optional[str]
    triggered_by: str
    is_critical: bool = False
    read_at: Optional[datetime] = None
    has_snapshot: bool = False

    model_config = {"from_attributes": True}


class SendResult(BaseModel):
    status: str
    recipients: list[str]
    article_count: int
    subject: str
    error: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────
async def _get_or_create_config(
    db: AsyncSession, revue_id: uuid.UUID
) -> NewsletterConfig:
    result = await db.execute(
        select(NewsletterConfig).where(NewsletterConfig.revue_id == revue_id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = NewsletterConfig(
            id=uuid.uuid4(),
            revue_id=revue_id,
            enabled=False,
            schedule_hour=8,
            schedule_minute=0,
            extra_recipients=[],
            include_client_email=True,
            subject_template="Revue de presse · {revue} · {date}",
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


def _config_to_out(config: NewsletterConfig) -> dict:
    return {
        "revue_id": str(config.revue_id),
        "enabled": config.enabled,
        "schedule_hour": config.schedule_hour,
        "schedule_minute": config.schedule_minute,
        "extra_recipients": config.extra_recipients or [],
        "include_client_email": config.include_client_email,
        "subject_template": config.subject_template,
        "last_sent_at": config.last_sent_at,
    }


def _log_to_out(log: EmailLog) -> dict:
    return {
        "id": str(log.id),
        "revue_id": str(log.revue_id),
        "sent_at": log.sent_at,
        "recipients": log.recipients or [],
        "article_count": log.article_count,
        "period_from": log.period_from,
        "period_to": log.period_to,
        "subject": log.subject,
        "status": log.status,
        "error_message": log.error_message,
        "triggered_by": log.triggered_by,
        "is_critical": getattr(log, "is_critical", False),
        "read_at": getattr(log, "read_at", None),
        "has_snapshot": bool(getattr(log, "html_snapshot", None)),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/config/{revue_id}")
async def get_config(
    revue_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_any_role),
):
    """Récupère la config newsletter d'une revue (crée si inexistante)."""
    rid = uuid.UUID(revue_id)
    config = await _get_or_create_config(db, rid)
    return _config_to_out(config)


@router.patch("/config/{revue_id}")
async def update_config(
    revue_id: str,
    body: NewsletterConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_admin_plus),
):
    """Met à jour la config newsletter. Recharge les jobs si le planning change."""
    rid = uuid.UUID(revue_id)
    config = await _get_or_create_config(db, rid)

    schedule_changed = False
    if body.enabled is not None:
        if body.enabled != config.enabled:
            schedule_changed = True
        config.enabled = body.enabled
    if body.schedule_hour is not None:
        if body.schedule_hour != config.schedule_hour:
            schedule_changed = True
        config.schedule_hour = body.schedule_hour
    if body.schedule_minute is not None:
        if body.schedule_minute != config.schedule_minute:
            schedule_changed = True
        config.schedule_minute = body.schedule_minute
    if body.extra_recipients is not None:
        config.extra_recipients = body.extra_recipients
    if body.include_client_email is not None:
        config.include_client_email = body.include_client_email
    if body.subject_template is not None:
        config.subject_template = body.subject_template

    await db.commit()
    await db.refresh(config)

    # Recharger les jobs si le planning a changé
    if schedule_changed:
        try:
            from app.main import reload_newsletter_jobs
            await reload_newsletter_jobs()
        except Exception as e:
            logger.warning(f"[newsletter] rechargement jobs ignoré: {e}")

    return _config_to_out(config)


@router.post("/send-now/{revue_id}")
async def send_now(
    revue_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_admin_plus),
):
    """Envoie la revue de presse immédiatement aux destinataires configurés."""
    from app.services.email_service import send_newsletter
    rid = uuid.UUID(revue_id)
    result = await send_newsletter(db, rid, triggered_by="manual")
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/test/{revue_id}")
async def send_test(
    revue_id: str,
    email: str = Query(..., description="Email de test"),
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_admin_plus),
):
    """Envoie un email de test à une adresse spécifique (sans modifier last_sent_at)."""
    from app.services.email_service import send_newsletter
    rid = uuid.UUID(revue_id)
    result = await send_newsletter(db, rid, triggered_by="test", test_email=email)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/preview/{revue_id}", response_class=HTMLResponse)
async def preview_newsletter(
    revue_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_any_role),
):
    """Retourne le HTML de la prochaine revue avec synthèse exécutive (pour prévisualisation)."""
    from app.services.email_service import _get_newsletter_data, _build_html, _generate_executive_summary
    from app.models.revue import Revue
    rid = uuid.UUID(revue_id)

    revue = await db.get(Revue, rid)
    if not revue:
        raise HTTPException(status_code=404, detail="Revue introuvable")

    config_result = await db.execute(
        select(NewsletterConfig).where(NewsletterConfig.revue_id == rid)
    )
    config = config_result.scalar_one_or_none()
    since = config.last_sent_at if config else None

    articles, keyword_names, period_from, period_to = await _get_newsletter_data(db, rid, since)
    date_str = period_to.strftime("%d/%m/%Y")
    executive_summary = await _generate_executive_summary(articles, revue.name, date_str, keyword_names)
    html = _build_html(revue.name, articles, keyword_names, period_from, period_to, executive_summary)
    return HTMLResponse(content=html)


@router.get("/logs/{revue_id}")
async def get_logs(
    revue_id: str,
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_any_role),
):
    """Historique des envois de newsletter pour une revue."""
    rid = uuid.UUID(revue_id)
    result = await db.execute(
        select(EmailLog)
        .where(EmailLog.revue_id == rid)
        .order_by(EmailLog.sent_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [_log_to_out(log) for log in logs]


# ── Inbox ─────────────────────────────────────────────────────────────────────

@router.get("/inbox")
async def get_inbox(
    db: AsyncSession = Depends(get_db),
    user: Account = Depends(require_any_role),
):
    """Boîte de réception newsletter — uniquement les envois réussis (status='sent')."""
    if user.client_id:
        revue_res = await db.execute(
            select(Revue.id).where(Revue.client_id == user.client_id)
        )
        revue_ids = [r[0] for r in revue_res.all()]
        if not revue_ids:
            return []
        result = await db.execute(
            select(EmailLog)
            .where(
                EmailLog.revue_id.in_(revue_ids),
                EmailLog.status == "sent",
            )
            .order_by(EmailLog.sent_at.desc())
            .limit(50)
        )
    else:
        # Admin : tous les logs réussis récents
        result = await db.execute(
            select(EmailLog)
            .where(EmailLog.status == "sent")
            .order_by(EmailLog.sent_at.desc())
            .limit(50)
        )
    logs = result.scalars().all()
    return [_log_to_out(log) for log in logs]


@router.get("/admin/logs")
async def get_admin_logs(
    revue_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    triggered_by: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_admin_plus),
):
    """Historique global de tous les envois newsletter (admin uniquement)."""
    query = select(EmailLog)
    conditions = []
    if revue_id:
        conditions.append(EmailLog.revue_id == uuid.UUID(revue_id))
    if status:
        conditions.append(EmailLog.status == status)
    if triggered_by:
        conditions.append(EmailLog.triggered_by == triggered_by)
    if conditions:
        query = query.where(*conditions)
    result = await db.execute(
        query.order_by(EmailLog.sent_at.desc()).limit(limit)
    )
    logs = result.scalars().all()
    return [_log_to_out(log) for log in logs]


@router.get("/admin/errors")
async def get_newsletter_errors(
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_admin_plus),
):
    """Erreurs d'envoi newsletter récentes (pour la page Santé système)."""
    result = await db.execute(
        select(EmailLog)
        .where(EmailLog.status == "error")
        .order_by(EmailLog.sent_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [_log_to_out(log) for log in logs]


@router.post("/inbox/{log_id}/read")
async def mark_inbox_read(
    log_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_any_role),
):
    """Marquer une newsletter comme lue dans la boîte de réception."""
    lid = uuid.UUID(log_id)
    log = await db.get(EmailLog, lid)
    if not log:
        raise HTTPException(status_code=404, detail="Log introuvable")
    if log.read_at is None:
        from datetime import timezone
        log.read_at = datetime.now(timezone.utc)
        await db.commit()
    return {"ok": True}


@router.get("/inbox/{log_id}/html", response_class=HTMLResponse)
async def get_inbox_html(
    log_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Account = Depends(require_any_role),
):
    """Retourne le HTML stocké d'une newsletter envoyée (inbox snapshot)."""
    lid = uuid.UUID(log_id)
    log = await db.get(EmailLog, lid)
    if not log:
        raise HTTPException(status_code=404, detail="Log introuvable")
    if not getattr(log, "html_snapshot", None):
        raise HTTPException(status_code=404, detail="Aucun snapshot disponible pour cette newsletter")
    return HTMLResponse(content=log.html_snapshot)


@router.get("/notifications/unread-count")
async def get_unread_critical_count(
    db: AsyncSession = Depends(get_db),
    user: Account = Depends(require_any_role),
):
    """Nombre d'alertes critiques non lues pour l'utilisateur courant."""
    from sqlalchemy import func
    if user.client_id:
        revue_res = await db.execute(
            select(Revue.id).where(Revue.client_id == user.client_id)
        )
        revue_ids = [r[0] for r in revue_res.all()]
        if not revue_ids:
            return {"unread_critical": 0}
        count_res = await db.execute(
            select(func.count(EmailLog.id)).where(
                EmailLog.revue_id.in_(revue_ids),
                EmailLog.is_critical == True,
                EmailLog.read_at.is_(None),
            )
        )
    else:
        count_res = await db.execute(
            select(func.count(EmailLog.id)).where(
                EmailLog.is_critical == True,
                EmailLog.read_at.is_(None),
            )
        )
    count = count_res.scalar_one_or_none() or 0
    return {"unread_critical": int(count)}

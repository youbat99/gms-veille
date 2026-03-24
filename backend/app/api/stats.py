"""
Endpoint de statistiques OPS pour le dashboard équipe GMS.
Accessible aux super_admin et admin uniquement.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.client import Account, AccountRole, Client, UserAccount
from app.models.revue import Revue
from app.models.article import Article, ArticleStatus

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/ops")
async def ops_stats(
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dashboard OPS GMS : vue d'ensemble de l'équipe interne.
    - super_admin : voit tous les clients / revues / valideurs
    - admin GMS   : voit uniquement son client
    """
    if current_user.role not in (AccountRole.super_admin, AccountRole.admin):
        raise HTTPException(403, "Réservé aux admins GMS")

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)

    # ── Filtre par client selon le rôle ───────────────────────────────────
    if current_user.role == AccountRole.admin:
        client_filter = [Client.id == current_user.client_id]
        revue_filter  = [Revue.client_id == current_user.client_id]
        account_filter = [
            Account.client_id == current_user.client_id,
            Account.role == AccountRole.validator,
        ]
    else:  # super_admin — tout voir
        client_filter  = []
        revue_filter   = []
        account_filter = [Account.role == AccountRole.validator]

    # ── Compteurs globaux ─────────────────────────────────────────────────
    active_validators = await db.scalar(
        select(func.count()).where(
            Account.is_active == True,
            *account_filter,
        )
    )

    active_clients = await db.scalar(
        select(func.count()).where(Client.is_active == True, *client_filter)
    ) if current_user.role == AccountRole.super_admin else 1

    active_revues = await db.scalar(
        select(func.count()).where(Revue.is_active == True, *revue_filter)
    )

    # Articles en attente total
    total_pending = await db.scalar(
        select(func.count()).select_from(Article)
        .join(Revue, Article.revue_id == Revue.id)
        .where(Article.status == ArticleStatus.pending, *revue_filter)
    )

    # Validés aujourd'hui total
    validated_today = await db.scalar(
        select(func.count()).select_from(Article)
        .join(Revue, Article.revue_id == Revue.id)
        .where(
            Article.status.in_([ArticleStatus.approved, ArticleStatus.modified]),
            Article.validated_at >= today_start,
            *revue_filter,
        )
    )

    # Erreurs total
    total_errors = await db.scalar(
        select(func.count()).select_from(Article)
        .join(Revue, Article.revue_id == Revue.id)
        .where(Article.status == ArticleStatus.error, *revue_filter)
    )

    # ── Performance par valideur ──────────────────────────────────────────
    validators_q = await db.execute(
        select(Account)
        .where(Account.is_active == True, *account_filter)
        .order_by(Account.full_name)
    )
    validators = validators_q.scalars().all()

    validator_stats = []
    for v in validators:
        today_count = await db.scalar(
            select(func.count()).where(
                Article.validated_by == v.id,
                Article.validated_at >= today_start,
            )
        )
        month_count = await db.scalar(
            select(func.count()).where(
                Article.validated_by == v.id,
                Article.validated_at >= month_start,
            )
        )
        validator_stats.append({
            "id": str(v.id),
            "full_name": v.full_name,
            "email": v.email,
            "client_id": str(v.client_id) if v.client_id else None,
            "validated_today": today_count or 0,
            "validated_month": month_count or 0,
        })

    # ── Stats par revue ───────────────────────────────────────────────────
    revues_q = await db.execute(
        select(Revue, Client)
        .join(Client, Revue.client_id == Client.id)
        .where(Revue.is_active == True, *revue_filter)
        .order_by(Client.name, Revue.name)
    )
    revues_rows = revues_q.all()

    revue_stats = []
    for revue, client in revues_rows:
        pending = await db.scalar(
            select(func.count()).where(
                Article.revue_id == revue.id,
                Article.status == ArticleStatus.pending,
            )
        )
        errors = await db.scalar(
            select(func.count()).where(
                Article.revue_id == revue.id,
                Article.status == ArticleStatus.error,
            )
        )
        val_today = await db.scalar(
            select(func.count()).where(
                Article.revue_id == revue.id,
                Article.status.in_([ArticleStatus.approved, ArticleStatus.modified]),
                Article.validated_at >= today_start,
            )
        )
        revue_stats.append({
            "revue_id": str(revue.id),
            "revue_name": revue.name,
            "client_name": client.name,
            "client_id": str(client.id),
            "pending": pending or 0,
            "errors": errors or 0,
            "validated_today": val_today or 0,
        })

    return {
        "summary": {
            "active_clients": active_clients or 0,
            "active_revues": active_revues or 0,
            "active_validators": active_validators or 0,
            "total_pending": total_pending or 0,
            "validated_today": validated_today or 0,
            "total_errors": total_errors or 0,
        },
        "validators": validator_stats,
        "revues": revue_stats,
    }

"""
Gestion des clients (organisations).
Seul le super_admin peut créer / modifier / désactiver des clients.
"""
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.deps import require_super_admin, require_admin_plus, get_current_user
from app.models.client import Client, Account, AccountRole, UserAccount
from app.models.revue import Revue, RevueKeyword
from app.models.article import Article, ArticleModificationLog
from app.models.article_read import ArticleRead

router = APIRouter(prefix="/clients", tags=["clients"])


# ── Schemas ────────────────────────────────────────────────────────────────

class ClientCreate(BaseModel):
    name: str
    email: EmailStr
    subscription_plan: str = "starter"


class ClientOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    is_active: bool
    subscription_plan: str
    created_at: datetime

    class Config:
        from_attributes = True


class ClientUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    is_active: bool | None = None
    subscription_plan: str | None = None


class AssignRevueBody(BaseModel):
    revue_id: uuid.UUID


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/", response_model=list[ClientOut])
async def list_clients(
    current_user: Account = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Client).order_by(Client.created_at.desc()))
    return result.scalars().all()


@router.post("/", response_model=ClientOut, status_code=201)
async def create_client(
    body: ClientCreate,
    current_user: Account = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.scalar(select(Client).where(Client.email == body.email))
    if existing:
        raise HTTPException(400, "Email client déjà utilisé")

    client = Client(
        name=body.name,
        email=body.email,
        subscription_plan=body.subscription_plan,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client


@router.get("/{client_id}/detail")
async def client_detail(
    client_id: uuid.UUID,
    current_user: Account = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Vue complète : client + revues avec mots-clés + comptes + accès revues."""
    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client introuvable")

    # Revues avec leurs mots-clés
    revues_result = await db.execute(
        select(Revue)
        .where(Revue.client_id == client_id)
        .options(selectinload(Revue.revue_keywords).selectinload(RevueKeyword.keyword))
        .order_by(Revue.name)
    )
    revues = revues_result.scalars().all()
    revue_map = {r.id: r.name for r in revues}

    # Comptes du client (client_admin + client_user)
    accounts_result = await db.execute(
        select(Account)
        .where(Account.client_id == client_id)
        .options(selectinload(Account.revue_accesses))
        .order_by(Account.role, Account.full_name)
    )
    accounts = accounts_result.scalars().all()

    return {
        "client": {
            "id": str(client.id),
            "name": client.name,
            "email": client.email,
            "is_active": client.is_active,
            "subscription_plan": client.subscription_plan,
            "created_at": client.created_at.isoformat(),
        },
        "revues": [
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat(),
                "keywords": [
                    {
                        "id": str(rk.keyword.id),
                        "term": rk.keyword.term,
                        "language": rk.keyword.language,
                        "type": rk.keyword.type.value,
                        "is_active": rk.keyword.is_active,
                    }
                    for rk in r.revue_keywords
                ],
            }
            for r in revues
        ],
        "accounts": [
            {
                "id": str(a.id),
                "email": a.email,
                "full_name": a.full_name,
                "role": a.role.value,
                "is_active": a.is_active,
                "created_at": a.created_at.isoformat(),
                "revue_accesses": [
                    {
                        "revue_id": str(ua.revue_id),
                        "revue_name": revue_map.get(ua.revue_id, "—"),
                        "can_export": ua.can_export,
                        "can_view_dashboard": ua.can_view_dashboard,
                    }
                    for ua in a.revue_accesses
                ],
            }
            for a in accounts
        ],
    }


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(
    client_id: uuid.UUID,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == AccountRole.admin and current_user.client_id != client_id:
        raise HTTPException(403, "Accès refusé")
    if current_user.role == AccountRole.validator:
        raise HTTPException(403, "Accès refusé")

    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client introuvable")
    return client


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: uuid.UUID,
    body: ClientUpdate,
    current_user: Account = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client introuvable")

    if body.name is not None:
        client.name = body.name
    if body.email is not None:
        # Vérifier unicité email
        conflict = await db.scalar(
            select(Client).where(Client.email == body.email, Client.id != client_id)
        )
        if conflict:
            raise HTTPException(400, "Email déjà utilisé par un autre client")
        client.email = body.email
    if body.is_active is not None:
        client.is_active = body.is_active
    if body.subscription_plan is not None:
        client.subscription_plan = body.subscription_plan

    await db.commit()
    await db.refresh(client)
    return client


@router.delete("/{client_id}", status_code=204)
async def delete_client(
    client_id: uuid.UUID,
    current_user: Account = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Suppression définitive d'un client (cascade complète : revues, articles, comptes)."""
    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client introuvable")

    # 1. IDs des revues du client
    revue_ids_res = await db.execute(select(Revue.id).where(Revue.client_id == client_id))
    revue_ids = [r[0] for r in revue_ids_res.all()]

    if revue_ids:
        # Sous-requête : IDs des articles de ces revues
        art_ids_subq = select(Article.id).where(Article.revue_id.in_(revue_ids))
        # 2. Logs de modification des articles
        await db.execute(delete(ArticleModificationLog).where(ArticleModificationLog.article_id.in_(art_ids_subq)))
        # 3. Lectures des articles
        await db.execute(delete(ArticleRead).where(ArticleRead.article_id.in_(art_ids_subq)))
        # 4. Articles
        await db.execute(delete(Article).where(Article.revue_id.in_(revue_ids)))
        # 5. Accès utilisateurs liés aux revues
        await db.execute(delete(UserAccount).where(UserAccount.revue_id.in_(revue_ids)))
        # 6. Mots-clés des revues
        await db.execute(delete(RevueKeyword).where(RevueKeyword.revue_id.in_(revue_ids)))
        # 7. Revues
        await db.execute(delete(Revue).where(Revue.client_id == client_id))

    # 8. IDs des comptes du client
    account_ids_res = await db.execute(select(Account.id).where(Account.client_id == client_id))
    account_ids = [r[0] for r in account_ids_res.all()]

    if account_ids:
        # 9. Accès utilisateurs restants (liés aux comptes)
        await db.execute(delete(UserAccount).where(UserAccount.account_id.in_(account_ids)))
        # 10. Comptes
        await db.execute(delete(Account).where(Account.client_id == client_id))

    # 11. Client
    await db.execute(delete(Client).where(Client.id == client_id))
    await db.commit()


@router.post("/{client_id}/revues/{revue_id}/assign", status_code=200)
async def assign_revue_to_client(
    client_id: uuid.UUID,
    revue_id: uuid.UUID,
    current_user: Account = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Réassigne une revue existante à ce client."""
    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client introuvable")
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")

    revue.client_id = client_id
    await db.commit()
    await db.refresh(revue)
    return {"status": "assigned", "revue_id": str(revue_id), "client_id": str(client_id)}


@router.get("/{client_id}/summary")
async def client_summary(
    client_id: uuid.UUID,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    """Nombre d'utilisateurs et de revues pour un client."""
    if current_user.role == AccountRole.admin and current_user.client_id != client_id:
        raise HTTPException(403, "Accès refusé")

    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client introuvable")

    user_count = await db.scalar(
        select(func.count()).where(Account.client_id == client_id, Account.is_active == True)
    )
    revue_count = await db.scalar(
        select(func.count()).where(Revue.client_id == client_id, Revue.is_active == True)
    )

    return {
        "client_id": str(client_id),
        "name": client.name,
        "user_count": user_count or 0,
        "revue_count": revue_count or 0,
        "subscription_plan": client.subscription_plan,
        "is_active": client.is_active,
    }

"""
Gestion des comptes utilisateurs (CRUD + accès revues).

Hiérarchie :
  super_admin  → crée admins GMS, valideurs, client_admin (liés à un client)
  admin GMS    → crée valideurs pour son client
  validator    → aucune gestion (lecture seule de son propre compte)
  client_admin → crée client_user pour son organisation + assigne revues
  client_user  → aucune gestion
"""
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.client import Account, AccountRole, Client, UserAccount
from app.models.revue import Revue
from app.services.auth_service import hash_password

router = APIRouter(prefix="/users", tags=["users"])


# ── Schemas ────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: AccountRole
    client_id: uuid.UUID | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: AccountRole
    client_id: uuid.UUID | None
    is_active: bool
    created_at: datetime
    created_by: uuid.UUID | None

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    password: str | None = None
    is_active: bool | None = None
    role: AccountRole | None = None


class RevueAccessOut(BaseModel):
    revue_id: uuid.UUID
    revue_name: str
    can_export: bool
    can_view_dashboard: bool

    class Config:
        from_attributes = True


class RevueAccessAssign(BaseModel):
    revue_id: uuid.UUID
    can_export: bool = False
    can_view_dashboard: bool = True


class RevueAccessUpdate(BaseModel):
    can_export: bool | None = None
    can_view_dashboard: bool | None = None


# ── Helpers ────────────────────────────────────────────────────────────────

GMS_ROLES = {AccountRole.super_admin, AccountRole.admin, AccountRole.validator}
CLIENT_ROLES = {AccountRole.client_admin, AccountRole.client_user}


def _can_manage_user(current: Account, target: Account) -> bool:
    """Vérifie si current_user peut gérer target_user."""
    if current.role == AccountRole.super_admin:
        return True
    if current.role == AccountRole.admin:
        return target.client_id == current.client_id and target.role == AccountRole.validator
    if current.role == AccountRole.client_admin:
        return target.client_id == current.client_id and target.role == AccountRole.client_user
    # validator et client_user ne gèrent personne
    return target.id == current.id


# ── Endpoints : comptes ────────────────────────────────────────────────────

@router.post("/", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.role

    # Rôles sans droit de création
    if role in (AccountRole.validator, AccountRole.client_user):
        raise HTTPException(403, "Accès refusé")

    # Déterminer le client_id final et vérifier les permissions de création
    if role == AccountRole.admin:
        # GMS admin → crée uniquement des valideurs GMS pour son client
        if body.role != AccountRole.validator:
            raise HTTPException(403, "Un admin GMS peut uniquement créer des valideurs")
        client_id = current_user.client_id

    elif role == AccountRole.client_admin:
        # Client admin → crée uniquement des client_user pour son org
        if body.role != AccountRole.client_user:
            raise HTTPException(403, "Un admin client peut uniquement créer des utilisateurs client")
        client_id = current_user.client_id

    elif role == AccountRole.super_admin:
        # Super admin → peut créer tous les rôles
        if body.role in (AccountRole.admin, AccountRole.validator,
                         AccountRole.client_admin, AccountRole.client_user):
            if not body.client_id:
                raise HTTPException(400, "client_id requis pour ce rôle")
        if body.role == AccountRole.super_admin:
            body.client_id = None
        client_id = body.client_id

    # Vérifier que le client existe
    if client_id:
        client = await db.get(Client, client_id)
        if not client:
            raise HTTPException(404, "Client introuvable")

    # Email unique
    existing = await db.scalar(select(Account).where(Account.email == body.email))
    if existing:
        raise HTTPException(400, "Email déjà utilisé")

    user = Account(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=body.role,
        client_id=client_id,
        created_by=current_user.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/", response_model=list[UserOut])
async def list_users(
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = current_user.role

    if role == AccountRole.super_admin:
        # Tous les comptes
        result = await db.execute(select(Account).order_by(Account.created_at.desc()))

    elif role == AccountRole.admin:
        # Valideurs GMS de son client uniquement
        result = await db.execute(
            select(Account)
            .where(Account.client_id == current_user.client_id,
                   Account.role == AccountRole.validator)
            .order_by(Account.created_at.desc())
        )

    elif role == AccountRole.client_admin:
        # Utilisateurs client de son organisation uniquement
        result = await db.execute(
            select(Account)
            .where(Account.client_id == current_user.client_id,
                   Account.role == AccountRole.client_user)
            .order_by(Account.created_at.desc())
        )

    else:
        raise HTTPException(403, "Accès refusé")

    return result.scalars().all()


@router.get("/me", response_model=UserOut)
async def get_me(current_user: Account = Depends(get_current_user)):
    return current_user


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(Account, user_id)
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")

    if not _can_manage_user(current_user, user) and user.id != current_user.id:
        raise HTTPException(403, "Accès refusé")
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(Account, user_id)
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")

    # Un utilisateur peut modifier son propre profil (nom + mot de passe)
    is_self = user.id == current_user.id
    if not is_self and not _can_manage_user(current_user, user):
        raise HTTPException(403, "Accès refusé")

    # Seul un gestionnaire peut modifier is_active (pas soi-même)
    if body.is_active is not None:
        if is_self:
            raise HTTPException(400, "Impossible de modifier son propre statut")
        if not _can_manage_user(current_user, user):
            raise HTTPException(403, "Accès refusé")
        user.is_active = body.is_active

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.password is not None:
        user.hashed_password = hash_password(body.password)
    if body.email is not None and not is_self:
        # Vérifier unicité email
        conflict = await db.scalar(select(Account).where(Account.email == body.email, Account.id != user_id))
        if conflict:
            raise HTTPException(400, "Email déjà utilisé")
        user.email = body.email
    if body.role is not None and not is_self:
        # Seul super_admin peut changer un rôle
        if current_user.role != AccountRole.super_admin:
            raise HTTPException(403, "Seul le super admin peut modifier un rôle")
        user.role = body.role

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role in (AccountRole.validator, AccountRole.client_user):
        raise HTTPException(403, "Accès refusé")

    user = await db.get(Account, user_id)
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    if user.id == current_user.id:
        raise HTTPException(400, "Impossible de supprimer son propre compte")
    if not _can_manage_user(current_user, user):
        raise HTTPException(403, "Accès refusé")

    user.is_active = False  # soft delete
    await db.commit()
    return {"status": "deactivated", "id": str(user_id)}


# ── Endpoints : accès revues ───────────────────────────────────────────────

@router.get("/{user_id}/revues", response_model=list[RevueAccessOut])
async def list_user_revue_accesses(
    user_id: uuid.UUID,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Liste les revues auxquelles un utilisateur a accès."""
    user = await db.get(Account, user_id)
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    if not _can_manage_user(current_user, user) and user.id != current_user.id:
        raise HTTPException(403, "Accès refusé")

    result = await db.execute(
        select(UserAccount, Revue)
        .join(Revue, UserAccount.revue_id == Revue.id)
        .where(UserAccount.account_id == user_id)
    )
    rows = result.all()
    return [
        RevueAccessOut(
            revue_id=ua.revue_id,
            revue_name=revue.name,
            can_export=ua.can_export,
            can_view_dashboard=ua.can_view_dashboard,
        )
        for ua, revue in rows
    ]


@router.post("/{user_id}/revues", status_code=201)
async def assign_revue_access(
    user_id: uuid.UUID,
    body: RevueAccessAssign,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Assigne l'accès à une revue pour un utilisateur."""
    user = await db.get(Account, user_id)
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    if not _can_manage_user(current_user, user):
        raise HTTPException(403, "Accès refusé")

    revue = await db.get(Revue, body.revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")

    # Vérifier que la revue appartient bien au même client
    if revue.client_id != user.client_id:
        raise HTTPException(400, "La revue n'appartient pas à ce client")

    # Vérifier si l'accès existe déjà
    existing = await db.get(UserAccount, (user_id, body.revue_id))
    if existing:
        raise HTTPException(400, "Accès déjà assigné")

    access = UserAccount(
        account_id=user_id,
        revue_id=body.revue_id,
        can_export=body.can_export,
        can_view_dashboard=body.can_view_dashboard,
    )
    db.add(access)
    await db.commit()
    return {"status": "assigned", "revue_id": str(body.revue_id)}


@router.patch("/{user_id}/revues/{revue_id}")
async def update_revue_access(
    user_id: uuid.UUID,
    revue_id: uuid.UUID,
    body: RevueAccessUpdate,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Modifie les permissions d'accès à une revue."""
    user = await db.get(Account, user_id)
    if not user or not _can_manage_user(current_user, user):
        raise HTTPException(403, "Accès refusé")

    access = await db.get(UserAccount, (user_id, revue_id))
    if not access:
        raise HTTPException(404, "Accès introuvable")

    if body.can_export is not None:
        access.can_export = body.can_export
    if body.can_view_dashboard is not None:
        access.can_view_dashboard = body.can_view_dashboard

    await db.commit()
    return {"status": "updated"}


@router.delete("/{user_id}/revues/{revue_id}")
async def remove_revue_access(
    user_id: uuid.UUID,
    revue_id: uuid.UUID,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retire l'accès à une revue pour un utilisateur."""
    user = await db.get(Account, user_id)
    if not user or not _can_manage_user(current_user, user):
        raise HTTPException(403, "Accès refusé")

    access = await db.get(UserAccount, (user_id, revue_id))
    if not access:
        raise HTTPException(404, "Accès introuvable")

    await db.delete(access)
    await db.commit()
    return {"status": "removed"}

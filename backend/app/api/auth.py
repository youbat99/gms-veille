"""
Endpoints d'authentification : login, me, refresh.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.client import Account, AccountRole
from app.services.auth_service import verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    id: uuid.UUID
    full_name: str
    role: AccountRole
    client_id: uuid.UUID | None


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: AccountRole
    client_id: uuid.UUID | None
    is_active: bool

    class Config:
        from_attributes = True


@router.post("/login", response_model=TokenOut)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Login par email + mot de passe. Retourne un JWT."""
    result = await db.execute(select(Account).where(Account.email == form.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Compte désactivé")

    token = create_access_token({
        "sub": str(user.id),
        "role": user.role,
        "client_id": str(user.client_id) if user.client_id else None,
        "full_name": user.full_name,
    })
    return TokenOut(
        access_token=token,
        id=user.id,
        full_name=user.full_name,
        role=user.role,
        client_id=user.client_id,
    )


@router.get("/me", response_model=MeOut)
async def me(user: Account = Depends(get_current_user)):
    """Retourne le profil de l'utilisateur connecté."""
    return user

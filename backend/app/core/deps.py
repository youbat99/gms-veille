"""
Dépendances FastAPI réutilisables pour l'authentification et les rôles.
"""
import uuid
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError

from app.core.database import get_db
from app.models.client import Account, AccountRole
from app.services.auth_service import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Account:
    try:
        payload = decode_token(token)
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token invalide")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")

    user = await db.get(Account, uuid.UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Compte introuvable ou désactivé")
    return user


def require_roles(*roles: AccountRole):
    """Dépendance paramétrique : vérifie que l'utilisateur a l'un des rôles donnés."""
    async def check(user: Account = Depends(get_current_user)) -> Account:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Accès refusé")
        return user
    return check


# Raccourcis — GMS interne
require_super_admin  = require_roles(AccountRole.super_admin)
require_admin_plus   = require_roles(AccountRole.super_admin, AccountRole.admin)
require_gms_staff    = require_roles(AccountRole.super_admin, AccountRole.admin, AccountRole.validator)

# Raccourcis — tous rôles
require_any_role     = require_roles(
    AccountRole.super_admin, AccountRole.admin, AccountRole.validator,
    AccountRole.client_admin, AccountRole.client_user,
)

# Raccourcis — côté client
require_client_admin = require_roles(AccountRole.client_admin)
require_client_side  = require_roles(AccountRole.client_admin, AccountRole.client_user)

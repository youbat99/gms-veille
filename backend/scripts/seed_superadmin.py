"""
Script de seed : crée le compte super_admin initial.
Usage : python scripts/seed_superadmin.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import AsyncSessionLocal
from app.models.client import Account, AccountRole
from app.services.auth_service import hash_password
from sqlalchemy import select

EMAIL = "admin@gms.ma"
PASSWORD = "gms2025!"
FULL_NAME = "Super Admin GMS"


async def seed():
    async with AsyncSessionLocal() as db:
        existing = await db.scalar(select(Account).where(Account.email == EMAIL))
        if existing:
            print(f"✓ Super admin déjà existant : {EMAIL}")
            return

        user = Account(
            email=EMAIL,
            full_name=FULL_NAME,
            hashed_password=hash_password(PASSWORD),
            role=AccountRole.super_admin,
            client_id=None,
        )
        db.add(user)
        await db.commit()
        print(f"✓ Super admin créé : {EMAIL} / {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(seed())

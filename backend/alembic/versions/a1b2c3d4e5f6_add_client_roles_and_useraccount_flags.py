"""add_client_roles_and_useraccount_flags

Revision ID: a1b2c3d4e5f6
Revises: 0128549c13e6
Create Date: 2026-03-11 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '6dd80ba72039'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Ajouter les nouveaux rôles à l'enum accountrole ──────────────────
    op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS accountrole")
    op.execute("""
        CREATE TYPE accountrole AS ENUM (
            'super_admin',
            'admin',
            'validator',
            'client_admin',
            'client_user'
        )
    """)
    op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE accountrole USING role::accountrole")

    # ── Ajouter can_view_dashboard à user_accounts ───────────────────────
    op.add_column('user_accounts', sa.Column('can_view_dashboard', sa.Boolean(), nullable=False, server_default='true'))


def downgrade() -> None:
    # Supprimer la colonne
    op.drop_column('user_accounts', 'can_view_dashboard')

    # Remettre l'enum avec seulement les 3 rôles GMS
    op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS accountrole")
    op.execute("CREATE TYPE accountrole AS ENUM ('super_admin', 'admin', 'validator')")
    op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE accountrole USING role::accountrole")

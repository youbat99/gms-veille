"""update_accounts_roles_nullable_client

Revision ID: 0128549c13e6
Revises: 9d3cb9127183
Create Date: 2026-03-10 16:04:23.736076

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0128549c13e6'
down_revision: Union[str, None] = '9d3cb9127183'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Recréer l'enum accountrole avec les nouveaux rôles (0 lignes dans accounts)
    op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS accountrole")
    op.execute("CREATE TYPE accountrole AS ENUM ('super_admin', 'admin', 'validator')")
    op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE accountrole USING role::accountrole")

    op.add_column('accounts', sa.Column('created_by', sa.UUID(), nullable=True))
    op.alter_column('accounts', 'client_id', existing_type=sa.UUID(), nullable=True)
    op.drop_constraint('accounts_email_key', 'accounts', type_='unique')
    op.create_index(op.f('ix_accounts_email'), 'accounts', ['email'], unique=True)
    op.create_foreign_key('fk_accounts_created_by', 'accounts', 'accounts', ['created_by'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_accounts_created_by', 'accounts', type_='foreignkey')
    op.drop_index(op.f('ix_accounts_email'), table_name='accounts')
    op.create_unique_constraint('accounts_email_key', 'accounts', ['email'])
    op.alter_column('accounts', 'client_id', existing_type=sa.UUID(), nullable=False)
    op.drop_column('accounts', 'created_by')
    op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS accountrole")
    op.execute("CREATE TYPE accountrole AS ENUM ('admin', 'client', 'support')")
    op.execute("ALTER TABLE accounts ALTER COLUMN role TYPE accountrole USING role::accountrole")

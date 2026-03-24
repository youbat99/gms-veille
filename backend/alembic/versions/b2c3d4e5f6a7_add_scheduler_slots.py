"""add scheduler_slots table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
import uuid

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scheduler_slot',
        sa.Column('id',         sa.UUID(),       nullable=False, primary_key=True),
        sa.Column('hour',       sa.Integer(),    nullable=False),
        sa.Column('minute',     sa.Integer(),    nullable=False, server_default='0'),
        sa.Column('label',      sa.String(64),   nullable=False, server_default=''),
        sa.Column('enabled',    sa.Boolean(),    nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    # Insère les slots par défaut (remplace les horaires hardcodés)
    op.execute("""
        INSERT INTO scheduler_slot (id, hour, minute, label, enabled)
        VALUES
          (gen_random_uuid(), 8,  0, 'Matin',          true),
          (gen_random_uuid(), 11, 0, 'Mi-journée',     true),
          (gen_random_uuid(), 14, 0, 'Après-midi',     true),
          (gen_random_uuid(), 17, 0, 'Fin de journée', true),
          (gen_random_uuid(), 0,  0, 'Minuit',         true)
    """)


def downgrade() -> None:
    op.drop_table('scheduler_slot')

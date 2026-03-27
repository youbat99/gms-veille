"""add deactivated_at and permanently_dead to media_sources

Revision ID: 9b2a93bfe188
Revises: 81ea8725ea9f
Create Date: 2026-03-27 20:25:39.947762

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '9b2a93bfe188'
down_revision: Union[str, None] = '81ea8725ea9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('media_sources', sa.Column(
        'deactivated_at', sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column('media_sources', sa.Column(
        'permanently_dead', sa.Boolean(), nullable=False, server_default='false'
    ))


def downgrade() -> None:
    op.drop_column('media_sources', 'permanently_dead')
    op.drop_column('media_sources', 'deactivated_at')

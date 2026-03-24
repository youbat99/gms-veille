"""add collection_method to articles

Revision ID: m2n3o4p5q6r7
Revises: l1m2n3o4p5q6
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = 'm2n3o4p5q6r7'
down_revision = 'l1m2n3o4p5q6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('articles', sa.Column('collection_method', sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column('articles', 'collection_method')

"""add entities_persons/orgs/places to articles

Revision ID: l1m2n3o4p5q6
Revises: k0l1m2n3o4p5
Create Date: 2026-03-17 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'l1m2n3o4p5q6'
down_revision = 'k0l1m2n3o4p5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('articles', sa.Column('entities_persons', postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column('articles', sa.Column('entities_orgs', postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column('articles', sa.Column('entities_places', postgresql.JSON(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('articles', 'entities_places')
    op.drop_column('articles', 'entities_orgs')
    op.drop_column('articles', 'entities_persons')

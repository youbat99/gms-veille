"""article: add image_url and meta_description (newspaper4k)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('articles', sa.Column('image_url', sa.String(length=2048), nullable=True))
    op.add_column('articles', sa.Column('meta_description', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('articles', 'meta_description')
    op.drop_column('articles', 'image_url')

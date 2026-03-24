"""add inbox fields to email_logs

Revision ID: ac7d8e9f0b1c
Revises: ab6c7d8e9f0a
Create Date: 2026-03-23 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'ac7d8e9f0b1c'
down_revision = 'ab6c7d8e9f0a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('email_logs', sa.Column('html_snapshot', sa.Text(), nullable=True))
    op.add_column('email_logs', sa.Column('article_ids', sa.JSON(), nullable=True, server_default='[]'))
    op.add_column('email_logs', sa.Column('is_critical', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('email_logs', sa.Column('read_at', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('email_logs', 'read_at')
    op.drop_column('email_logs', 'is_critical')
    op.drop_column('email_logs', 'article_ids')
    op.drop_column('email_logs', 'html_snapshot')

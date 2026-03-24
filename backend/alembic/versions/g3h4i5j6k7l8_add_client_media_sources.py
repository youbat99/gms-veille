"""add client_media_sources table

Revision ID: g3h4i5j6k7l8
Revises: c2d5e8f1a4b7
Create Date: 2026-03-15 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'g3h4i5j6k7l8'
down_revision = 'c2d5e8f1a4b7'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'client_media_sources',
        sa.Column('client_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('clients.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('media_sources.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

def downgrade() -> None:
    op.drop_table('client_media_sources')

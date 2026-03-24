"""add newsletter tables

Revision ID: y4z5a6b7c8d9
Revises: x3y4z5a6b7c8
Create Date: 2026-03-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'y4z5a6b7c8d9'
down_revision = 'x3y4z5a6b7c8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'newsletter_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revue_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('schedule_hour', sa.Integer(), nullable=False, server_default='8'),
        sa.Column('schedule_minute', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('extra_recipients', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('include_client_email', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('subject_template', sa.String(300), nullable=False, server_default='Revue de presse · {revue} · {date}'),
        sa.Column('last_sent_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['revue_id'], ['revues.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('revue_id'),
    )

    op.create_table(
        'email_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revue_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sent_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('recipients', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('article_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('period_from', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('period_to', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('subject', sa.String(300), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='sent'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('triggered_by', sa.String(20), nullable=False, server_default='manual'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['revue_id'], ['revues.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_email_logs_revue_id', 'email_logs', ['revue_id'])


def downgrade():
    op.drop_index('ix_email_logs_revue_id', table_name='email_logs')
    op.drop_table('email_logs')
    op.drop_table('newsletter_configs')

"""scheduler per-revue + collection_log table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Modifier scheduler_slot ──────────────────────────────────────
    # Ajouter les nouvelles colonnes (nullable d'abord)
    op.add_column('scheduler_slot',
        sa.Column('revue_id', sa.UUID(), nullable=True))
    op.add_column('scheduler_slot',
        sa.Column('tbs', sa.String(32), nullable=False, server_default='qdr:d'))
    op.add_column('scheduler_slot',
        sa.Column('language', sa.String(8), nullable=False, server_default='fr'))
    op.add_column('scheduler_slot',
        sa.Column('num_results', sa.Integer(), nullable=False, server_default='100'))

    # Supprimer les anciens créneaux globaux (sans revue)
    op.execute("DELETE FROM scheduler_slot WHERE revue_id IS NULL")

    # Rendre revue_id NOT NULL et ajouter la FK
    op.alter_column('scheduler_slot', 'revue_id', nullable=False)
    op.create_foreign_key(
        'fk_scheduler_slot_revue_id',
        'scheduler_slot', 'revues',
        ['revue_id'], ['id'],
        ondelete='CASCADE',
    )

    # ── 2. Créer collection_log ─────────────────────────────────────────
    op.create_table(
        'collection_log',
        sa.Column('id',           sa.UUID(),       nullable=False, primary_key=True),
        sa.Column('revue_id',     sa.UUID(),       nullable=True),
        sa.Column('revue_name',   sa.String(255),  nullable=False, server_default=''),
        sa.Column('triggered_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('finished_at',  sa.DateTime(timezone=True), nullable=True),
        sa.Column('trigger',      sa.String(16),   nullable=False, server_default='manual'),
        sa.Column('tbs',          sa.String(32),   nullable=True),
        sa.Column('collected',    sa.Integer(),    nullable=False, server_default='0'),
        sa.Column('errors',       sa.Integer(),    nullable=False, server_default='0'),
        sa.Column('duplicates',   sa.Integer(),    nullable=False, server_default='0'),
        sa.Column('status',       sa.String(16),   nullable=False, server_default='success'),
        sa.Column('duration_ms',  sa.Integer(),    nullable=True),
        sa.ForeignKeyConstraint(['revue_id'], ['revues.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_collection_log_triggered_at', 'collection_log', ['triggered_at'])
    op.create_index('ix_collection_log_revue_id', 'collection_log', ['revue_id'])


def downgrade() -> None:
    op.drop_table('collection_log')
    op.drop_constraint('fk_scheduler_slot_revue_id', 'scheduler_slot', type_='foreignkey')
    op.drop_column('scheduler_slot', 'revue_id')
    op.drop_column('scheduler_slot', 'tbs')
    op.drop_column('scheduler_slot', 'language')
    op.drop_column('scheduler_slot', 'num_results')
    # Re-seed les slots globaux par défaut
    op.execute("""
        INSERT INTO scheduler_slot (id, hour, minute, label, enabled)
        VALUES
          (gen_random_uuid(), 8,  0, 'Matin',          true),
          (gen_random_uuid(), 11, 0, 'Mi-journée',     true),
          (gen_random_uuid(), 14, 0, 'Après-midi',     true),
          (gen_random_uuid(), 17, 0, 'Fin de journée', true),
          (gen_random_uuid(), 0,  0, 'Minuit',         true)
    """)

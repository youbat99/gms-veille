"""fix collected_at default to now() in rss_articles

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u0
Create Date: 2026-03-19

La migration précédente avait figé collected_at à un timestamp statique
('2026-03-15 19:54:38') au lieu de now() dynamique. Tous les nouveaux
articles héritaient de cette vieille date, cassant le tri par date.
"""
from alembic import op

revision = "q6r7s8t9u0v1"
down_revision = "p5q6r7s8t9u0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE rss_articles ALTER COLUMN collected_at SET DEFAULT now()")


def downgrade() -> None:
    # On remet une valeur arbitraire — en pratique jamais utilisé
    op.execute("ALTER TABLE rss_articles ALTER COLUMN collected_at SET DEFAULT now()")

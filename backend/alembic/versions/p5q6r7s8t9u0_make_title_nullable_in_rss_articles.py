"""make title nullable in rss_articles (url-first pipeline)

Revision ID: p5q6r7s8t9u0
Revises: o4p5q6r7s8t9
Create Date: 2026-03-19

Le nouveau pipeline sauvegarde l'URL seule (title=NULL) puis
le worker d'extraction remplit le titre via Newspaper4k/Trafilatura.
"""
from alembic import op
import sqlalchemy as sa

revision = "p5q6r7s8t9u0"
down_revision = "o4p5q6r7s8t9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "rss_articles",
        "title",
        existing_type=sa.String(1024),
        nullable=True,
    )


def downgrade() -> None:
    # Remettre NOT NULL — attention: les lignes title=NULL planteront
    op.execute("UPDATE rss_articles SET title = url WHERE title IS NULL")
    op.alter_column(
        "rss_articles",
        "title",
        existing_type=sa.String(1024),
        nullable=False,
    )

"""refactor: add SERP params to scheduler_slot, drop interval/frequency from keywords

Revision ID: a1b2c3d4e5f7
Revises: f6a7b8c9d0e1
Create Date: 2026-03-13

Changes:
  - scheduler_slot: ADD engine, gl, sort_by, safe_search (new SerpAPI params)
  - revue_keywords:  DROP interval_hours (moved to scheduler_slot)
  - keywords:        DROP frequency column + DROP searchfrequency enum (if exist)
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f7'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def _col_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name=:t AND column_name=:c"
    ), {"t": table, "c": column}).fetchone()
    return result is not None


def upgrade() -> None:
    # ── scheduler_slot : nouveaux champs SerpAPI ─────────────────────
    if not _col_exists("scheduler_slot", "engine"):
        op.add_column("scheduler_slot",
            sa.Column("engine", sa.String(32), nullable=False, server_default="google_news"))

    if not _col_exists("scheduler_slot", "gl"):
        op.add_column("scheduler_slot",
            sa.Column("gl", sa.String(8), nullable=False, server_default="ma"))

    if not _col_exists("scheduler_slot", "sort_by"):
        op.add_column("scheduler_slot",
            sa.Column("sort_by", sa.String(16), nullable=False, server_default="relevance"))

    if not _col_exists("scheduler_slot", "safe_search"):
        op.add_column("scheduler_slot",
            sa.Column("safe_search", sa.Boolean(), nullable=False, server_default="true"))

    # ── revue_keywords : supprimer interval_hours (géré par scheduler) ──
    if _col_exists("revue_keywords", "interval_hours"):
        op.drop_column("revue_keywords", "interval_hours")

    if _col_exists("revue_keywords", "max_results_per_run"):
        op.drop_column("revue_keywords", "max_results_per_run")

    # ── keywords : supprimer frequency ──────────────────────────────
    if _col_exists("keywords", "frequency"):
        op.drop_column("keywords", "frequency")

    # Drop the enum type if it exists
    op.execute("DROP TYPE IF EXISTS searchfrequency")


def downgrade() -> None:
    # Recréer frequency sur keywords
    conn = op.get_bind()
    enum_exists = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname='searchfrequency'"
    )).fetchone()
    if not enum_exists:
        op.execute("CREATE TYPE searchfrequency AS ENUM ('low','medium','high','realtime')")

    if not _col_exists("keywords", "frequency"):
        op.add_column("keywords",
            sa.Column("frequency", sa.String(16), nullable=True))

    if not _col_exists("revue_keywords", "interval_hours"):
        op.add_column("revue_keywords",
            sa.Column("interval_hours", sa.Integer(), nullable=False, server_default="2"))

    # Supprimer les nouveaux champs de scheduler_slot
    for col in ["safe_search", "sort_by", "gl", "engine"]:
        if _col_exists("scheduler_slot", col):
            op.drop_column("scheduler_slot", col)

"""add raw GRID series state snapshots

Revision ID: 0002_grid_raw_series_states
Revises: 0001_initial
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_grid_raw_series_states"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grid_raw_series_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("grid_series_id", sa.String(length=120), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("source_endpoint", sa.String(length=120), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("has_games", sa.Boolean(), nullable=False),
        sa.Column("has_maps", sa.Boolean(), nullable=False),
        sa.Column("has_players", sa.Boolean(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_grid_raw_series_states_grid_series_id", "grid_raw_series_states", ["grid_series_id"])
    op.create_index("ix_grid_raw_series_states_fetched_at", "grid_raw_series_states", ["fetched_at"])
    op.create_index("ix_grid_raw_series_states_content_hash", "grid_raw_series_states", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_grid_raw_series_states_content_hash", table_name="grid_raw_series_states")
    op.drop_index("ix_grid_raw_series_states_fetched_at", table_name="grid_raw_series_states")
    op.drop_index("ix_grid_raw_series_states_grid_series_id", table_name="grid_raw_series_states")
    op.drop_table("grid_raw_series_states")

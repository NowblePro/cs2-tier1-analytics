"""add GRID stats snapshots

Revision ID: 0005_grid_stats_snapshots
Revises: 0004_grid_entity_maps
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_grid_stats_snapshots"
down_revision = "0004_grid_entity_maps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "grid_stats_snapshots" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "grid_stats_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("grid_id", sa.String(length=160), nullable=False),
        sa.Column("local_table", sa.String(length=80), nullable=True),
        sa.Column("local_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("window_name", sa.String(length=40), nullable=False),
        sa.Column("source_endpoint", sa.String(length=120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("entity_type", "grid_id", "window_name", name="uq_grid_stats_entity_window"),
    )
    op.create_index("ix_grid_stats_snapshots_entity_type", "grid_stats_snapshots", ["entity_type"])
    op.create_index("ix_grid_stats_snapshots_grid_id", "grid_stats_snapshots", ["grid_id"])
    op.create_index("ix_grid_stats_snapshots_local_id", "grid_stats_snapshots", ["local_id"])
    op.create_index("ix_grid_stats_snapshots_name", "grid_stats_snapshots", ["name"])
    op.create_index("ix_grid_stats_snapshots_window_name", "grid_stats_snapshots", ["window_name"])
    op.create_index("ix_grid_stats_snapshots_fetched_at", "grid_stats_snapshots", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_grid_stats_snapshots_fetched_at", table_name="grid_stats_snapshots")
    op.drop_index("ix_grid_stats_snapshots_window_name", table_name="grid_stats_snapshots")
    op.drop_index("ix_grid_stats_snapshots_name", table_name="grid_stats_snapshots")
    op.drop_index("ix_grid_stats_snapshots_local_id", table_name="grid_stats_snapshots")
    op.drop_index("ix_grid_stats_snapshots_grid_id", table_name="grid_stats_snapshots")
    op.drop_index("ix_grid_stats_snapshots_entity_type", table_name="grid_stats_snapshots")
    op.drop_table("grid_stats_snapshots")

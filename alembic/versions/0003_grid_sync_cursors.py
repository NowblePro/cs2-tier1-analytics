"""add GRID sync cursors

Revision ID: 0003_grid_sync_cursors
Revises: 0002_grid_raw_series_states
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_grid_sync_cursors"
down_revision = "0002_grid_raw_series_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "grid_sync_cursors" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "grid_sync_cursors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("date_from", sa.DateTime(), nullable=True),
        sa.Column("date_to", sa.DateTime(), nullable=True),
        sa.Column("last_successful_to", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_result_json", sa.Text(), nullable=True),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_grid_sync_cursors_name", "grid_sync_cursors", ["name"])
    op.create_index("ix_grid_sync_cursors_last_successful_to", "grid_sync_cursors", ["last_successful_to"])


def downgrade() -> None:
    op.drop_index("ix_grid_sync_cursors_last_successful_to", table_name="grid_sync_cursors")
    op.drop_index("ix_grid_sync_cursors_name", table_name="grid_sync_cursors")
    op.drop_table("grid_sync_cursors")

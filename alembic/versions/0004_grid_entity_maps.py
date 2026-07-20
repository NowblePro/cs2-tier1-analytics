"""add GRID entity map

Revision ID: 0004_grid_entity_maps
Revises: 0003_grid_sync_cursors
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_grid_entity_maps"
down_revision = "0003_grid_sync_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grid_entity_maps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("grid_id", sa.String(length=160), nullable=False),
        sa.Column("local_table", sa.String(length=80), nullable=True),
        sa.Column("local_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("entity_type", "grid_id", name="uq_grid_entity"),
    )
    op.create_index("ix_grid_entity_maps_entity_type", "grid_entity_maps", ["entity_type"])
    op.create_index("ix_grid_entity_maps_grid_id", "grid_entity_maps", ["grid_id"])
    op.create_index("ix_grid_entity_maps_local_id", "grid_entity_maps", ["local_id"])
    op.create_index("ix_grid_entity_maps_name", "grid_entity_maps", ["name"])


def downgrade() -> None:
    op.drop_index("ix_grid_entity_maps_name", table_name="grid_entity_maps")
    op.drop_index("ix_grid_entity_maps_local_id", table_name="grid_entity_maps")
    op.drop_index("ix_grid_entity_maps_grid_id", table_name="grid_entity_maps")
    op.drop_index("ix_grid_entity_maps_entity_type", table_name="grid_entity_maps")
    op.drop_table("grid_entity_maps")

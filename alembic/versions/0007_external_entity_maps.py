"""Add generic external provider entity mappings."""

from alembic import op
import sqlalchemy as sa

revision = "0007_external_entity_maps"
down_revision = "0006_job_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "external_entity_maps" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "external_entity_maps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=False),
        sa.Column("local_table", sa.String(length=80), nullable=False),
        sa.Column("local_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("provider", "entity_type", "external_id", name="uq_external_entity"),
    )
    op.create_index("ix_external_entity_maps_provider", "external_entity_maps", ["provider"])
    op.create_index("ix_external_entity_maps_entity_type", "external_entity_maps", ["entity_type"])
    op.create_index("ix_external_entity_maps_external_id", "external_entity_maps", ["external_id"])
    op.create_index("ix_external_entity_maps_local_id", "external_entity_maps", ["local_id"])
    op.create_index("ix_external_entity_maps_name", "external_entity_maps", ["name"])


def downgrade() -> None:
    op.drop_table("external_entity_maps")

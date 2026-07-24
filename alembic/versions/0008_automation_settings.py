"""Add persistent automation settings."""

from alembic import op
import sqlalchemy as sa

revision = "0008_automation_settings"
down_revision = "0007_external_entity_maps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "automation_settings" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "automation_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("upcoming_days", sa.Integer(), nullable=False),
        sa.Column("results_days", sa.Integer(), nullable=False),
        sa.Column("top_limit", sa.Integer(), nullable=False),
        sa.Column("max_matches", sa.Integer(), nullable=False),
        sa.Column("refresh_stats", sa.Boolean(), nullable=False),
        sa.Column("last_started_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("automation_settings")

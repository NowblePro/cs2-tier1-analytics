"""add persistent job runs

Revision ID: 0006_job_runs
Revises: 0005_grid_stats_snapshots
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_job_runs"
down_revision = "0005_grid_stats_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "job_runs" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("progress_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.UniqueConstraint("job_id", name="uq_job_runs_job_id"),
    )
    op.create_index("ix_job_runs_job_id", "job_runs", ["job_id"])
    op.create_index("ix_job_runs_kind", "job_runs", ["kind"])
    op.create_index("ix_job_runs_status", "job_runs", ["status"])
    op.create_index("ix_job_runs_created_at", "job_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_job_runs_created_at", table_name="job_runs")
    op.drop_index("ix_job_runs_status", table_name="job_runs")
    op.drop_index("ix_job_runs_kind", table_name="job_runs")
    op.drop_index("ix_job_runs_job_id", table_name="job_runs")
    op.drop_table("job_runs")

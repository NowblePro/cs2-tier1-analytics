"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.models.schema import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    from app.models.schema import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind)


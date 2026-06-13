"""add messages.entity_links

Revision ID: 20260613_0001
Revises: 20260612_0001
Create Date: 2026-06-13
"""
import sqlalchemy as sa
from alembic import op

revision = "20260613_0001"
down_revision = "20260612_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("entity_links", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("messages", "entity_links")

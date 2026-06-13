"""wire enum->text; extraction profiles; notion sync tables

Revision ID: 20260612_0001
Revises: backfill_protocols_to_orgs
Create Date: 2026-06-12
"""
import sqlalchemy as sa
from alembic import op

revision = "20260612_0001"
down_revision = "backfill_protocols_to_orgs"
branch_labels = None
depends_on = None


def upgrade():
    # Wire becomes user-defined config, not schema. USING cast preserves values.
    for table in ("messages", "summaries", "channel_configs"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN wire TYPE TEXT USING wire::text")
    op.execute("DROP TYPE IF EXISTS wire")

    op.add_column("channel_configs",
        sa.Column("extraction_profile", sa.String(32), nullable=False, server_default="grant"))
    op.add_column("channel_configs", sa.Column("prompt_hint", sa.Text(), nullable=True))
    op.add_column("channel_configs", sa.Column("notion_database_id", sa.String(64), nullable=True))

    op.create_table(
        "notion_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("summary_id", sa.Integer(), sa.ForeignKey("summaries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("summary_id", "item_index", name="uq_notion_pages_summary_item"),
    )
    op.create_index("ix_notion_pages_summary_id", "notion_pages", ["summary_id"])


def downgrade():
    op.drop_table("notion_pages")
    op.drop_column("channel_configs", "notion_database_id")
    op.drop_column("channel_configs", "prompt_hint")
    op.drop_column("channel_configs", "extraction_profile")
    op.execute("CREATE TYPE wire AS ENUM ('crypto', 'ai', 'oss')")
    for table in ("messages", "summaries", "channel_configs"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN wire TYPE wire USING wire::wire")

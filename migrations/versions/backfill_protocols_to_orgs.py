"""Backfill entities.protocols to entities.orgs

This migration renames the 'protocols' key to 'orgs' in the entities JSON column
of the summaries table. The dual-read support in the API ensures zero downtime.

Revision ID: backfill_protocols_to_orgs
Revises: 
Create Date: 2026-02-16

"""
from alembic import op
import sqlalchemy as sa

revision = 'backfill_protocols_to_orgs'
down_revision = '001_channel_config_fk'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE summaries
        SET entities = (
            (entities::jsonb - 'protocols')
            || jsonb_build_object('orgs', entities::jsonb->'protocols')
        )::json
        WHERE entities::jsonb ? 'protocols'
          AND NOT (entities::jsonb ? 'orgs')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE summaries
        SET entities = (
            (entities::jsonb - 'orgs')
            || jsonb_build_object('protocols', entities::jsonb->'orgs')
        )::json
        WHERE entities::jsonb ? 'orgs'
          AND NOT (entities::jsonb ? 'protocols')
    """)
